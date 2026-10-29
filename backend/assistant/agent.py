"""The assistant agent loop and streaming handler.

A bounded native-function-calling loop (max 6 tool rounds per turn).
The LLM is advisory only:
- Read tools execute scoped to request.user with RBAC checks.
- Zero write tools.
- Transaction creations go through proposal builders and are saved only by the human.
- Step-by-step progress is streamed as Server-Sent Events (SSE).
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, Generator, List, Optional
import urllib.request
import urllib.error

from django.conf import settings
from django.utils import timezone

from .models import AssistantConversation, AssistantMessage, AssistantProposal
from .prompts import SYSTEM_PROMPT, FALLBACK_REPLY, LOOP_EXHAUSTED_REPLY
from .tools import READ_TOOLS, CONTROL_TOOLS
from .proposals import PROPOSAL_BUILDERS

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
LLM_TIMEOUT_SECONDS = int(os.getenv("ASSISTANT_LLM_TIMEOUT", "20"))
MAX_HISTORY_TOKENS = int(os.getenv("ASSISTANT_MAX_HISTORY_TOKENS", "4000"))
CHARS_PER_TOKEN = 3

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_customers",
            "description": "Search customers by name, phone, or GSTIN.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Customer name or search query"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_suppliers",
            "description": "Search suppliers by name, phone, or GSTIN.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Supplier name or search query"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_items",
            "description": "Search items in catalogue by name, item code, or HSN code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Item name or code to search"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_employees",
            "description": "Search employees by name, mobile number, or employee ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Employee name or mobile"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock",
            "description": "Get current actual stock balance in warehouse for an item from Bin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_code": {"type": "string", "description": "Item code or name"},
                    "warehouse": {"type": "string", "description": "Optional warehouse name"},
                },
                "required": ["item_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_party_outstanding",
            "description": "Get outstanding balance and open invoices for a Customer or Supplier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "party_type": {"type": "string", "enum": ["Customer", "Supplier"]},
                    "party_id": {"type": "string", "description": "Party name or ID"},
                },
                "required": ["party_type", "party_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rate",
            "description": "Fetch resolved price list rate or standard rate for an item.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_code": {"type": "string", "description": "Item code or name"},
                    "party_type": {"type": "string", "enum": ["Customer", "Supplier"]},
                    "party_id": {"type": "string", "description": "Party name or ID"},
                },
                "required": ["item_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_documents",
            "description": "List recent transactions (SalesInvoice, PurchaseInvoice, PaymentEntry).",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": ["SalesInvoice", "PurchaseInvoice", "PaymentEntry"]},
                    "party_id": {"type": "string", "description": "Optional party id"},
                    "days": {"type": "integer", "description": "Days back to search (default 30)"},
                },
                "required": ["doctype"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document",
            "description": "Get details of a specific document by doctype and ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": ["SalesInvoice", "PurchaseInvoice", "PaymentEntry"]},
                    "id": {"type": "string", "description": "Document name or ID"},
                },
                "required": ["doctype", "id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user to clarify an ambiguity with clickable options.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Clarification question"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of choices for the user to tap",
                    },
                },
                "required": ["question", "options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Navigate user to an allowed list or form route.",
            "parameters": {
                "type": "object",
                "properties": {
                    "route": {"type": "string", "description": "The allowed in-app route"}
                },
                "required": ["route"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_sales_invoice",
            "description": "Prepare a Sales Invoice form for user review. Never writes to DB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_query": {"type": "string", "description": "Customer name or ID"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "string", "description": "Item name or code"},
                                "qty": {"type": "number", "description": "Quantity sold"},
                                "rate": {"type": "number", "description": "Optional explicit rate per unit"},
                            },
                            "required": ["item", "qty"],
                        },
                        "description": "Line items sold",
                    },
                    "posting_date": {"type": "string", "description": "Optional posting date YYYY-MM-DD"},
                    "remarks": {"type": "string", "description": "Optional remarks"},
                },
                "required": ["customer_query", "items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_purchase_invoice",
            "description": "Prepare a Purchase Invoice form for user review. Never writes to DB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "supplier_query": {"type": "string", "description": "Supplier name or ID"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "string", "description": "Item name or code"},
                                "qty": {"type": "number", "description": "Quantity bought"},
                                "rate": {"type": "number", "description": "Optional unit rate"},
                            },
                            "required": ["item", "qty"],
                        },
                        "description": "Line items bought",
                    },
                    "bill_no": {"type": "string", "description": "Supplier bill / invoice number"},
                    "bill_date": {"type": "string", "description": "Supplier bill date YYYY-MM-DD"},
                    "bill_total": {"type": "number", "description": "Total amount stated on supplier bill"},
                    "remarks": {"type": "string", "description": "Optional remarks"},
                },
                "required": ["supplier_query", "items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_payment_entry",
            "description": "Prepare a Payment Entry form for user review. Never writes to DB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "party_type": {"type": "string", "enum": ["Customer", "Supplier"]},
                    "party_query": {"type": "string", "description": "Customer or supplier name"},
                    "amount": {"type": "number", "description": "Payment amount"},
                    "payment_type": {"type": "string", "enum": ["Receive", "Pay"], "default": "Receive"},
                    "mode_of_payment": {"type": "string", "description": "Cash, Bank, Cheque"},
                },
                "required": ["party_type", "party_query", "amount"],
            },
        },
    },
]


def sse_event(event: str, data: Dict[str, Any]) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


class Agent:
    """Orchestrates an assistant turn."""

    def __init__(self, user, completion_fn: Optional[Callable] = None):
        self.user = user
        self.completion_fn = completion_fn or self._default_llm_completion

    def _default_llm_completion(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Call LLM API (OpenRouter or Anthropic or OpenAI-compatible). Returns dict with content/tool_calls."""
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("No LLM API key configured.")
            return {"content": FALLBACK_REPLY, "tool_calls": None}

        provider = (os.getenv("ASSISTANT_MODEL_PROVIDER") or "openrouter").lower()
        model = os.getenv("ASSISTANT_LLM_MODEL") or "anthropic/claude-3.5-sonnet"

        endpoint = os.getenv("ASSISTANT_LLM_ENDPOINT") or "https://openrouter.ai/api/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if "openrouter.ai" in endpoint:
            headers["HTTP-Referer"] = "https://erp.local"
            headers["X-Title"] = "ERP Assistant"

        payload = {
            "model": model,
            "messages": messages,
            "tools": TOOL_DEFINITIONS,
            "tool_choice": "auto",
            "temperature": 0.1,
            "max_tokens": 1000,
        }

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choice = result.get("choices", [{}])[0].get("message", {})
                return {
                    "content": choice.get("content") or "",
                    "tool_calls": choice.get("tool_calls"),
                }
        except Exception as exc:
            logger.warning("LLM API call failed: %s", exc)
            return {"content": FALLBACK_REPLY, "tool_calls": None}

    def _execute_tool(self, name: str, args: Dict[str, Any], message_obj: Optional[AssistantMessage] = None) -> Dict[str, Any]:
        """Execute a tool strictly checking permissions."""
        if name in READ_TOOLS:
            fn = READ_TOOLS[name]
            return fn(self.user, **args)
        if name in CONTROL_TOOLS:
            fn = CONTROL_TOOLS[name]
            return fn(**args)
        if name in PROPOSAL_BUILDERS:
            fn = PROPOSAL_BUILDERS[name]
            return fn(self.user, message=message_obj, **args)

        return {"error": "unknown_tool", "message": f"Tool '{name}' is not registered."}

    def run_stream(
        self,
        user_text: str,
        conversation: AssistantConversation,
        input_mode: str = "text",
        language: str = "en",
    ) -> Generator[str, None, None]:
        """Generator running the agent loop and yielding SSE events."""
        # 1. Save user turn
        user_msg = AssistantMessage.objects.create(
            conversation=conversation,
            role="user",
            content=user_text,
            input_mode=input_mode,
        )

        yield sse_event("meta", {"conversation_id": conversation.pk})

        # 2. Build history
        history_msgs = []
        recent = conversation.messages.order_by("-created_at")[:10]
        for m in reversed(recent):
            if m.role in ["user", "assistant"]:
                history_msgs.append({"role": m.role, "content": m.content or ""})

        messages = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\nUser preferred language: {language}"},
            *history_msgs,
        ]

        # Create placeholder for assistant turn
        asst_msg = AssistantMessage.objects.create(
            conversation=conversation,
            role="assistant",
            content="",
            steps=[],
        )

        steps_trace: List[Dict[str, Any]] = []
        final_reply = ""
        last_proposal = None

        try:
            for round_idx in range(MAX_TOOL_ROUNDS):
                llm_out = self.completion_fn(messages)
                content = llm_out.get("content") or ""
                tool_calls = llm_out.get("tool_calls") or []

                if not tool_calls:
                    final_reply = content
                    yield sse_event("reply", {"text": final_reply})
                    break

                # Handle tool calls
                asst_turn_msg = {"role": "assistant", "content": content, "tool_calls": tool_calls}
                messages.append(asst_turn_msg)

                for tc in tool_calls:
                    fn_info = tc.get("function", {})
                    fn_name = fn_info.get("name", "")
                    raw_args = fn_info.get("arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            parsed_args = {}
                    else:
                        parsed_args = raw_args

                    # Emit step: running
                    args_preview = ", ".join(f"{k}={v}" for k, v in list(parsed_args.items())[:2])
                    step_entry = {
                        "key": fn_name,
                        "label_key": f"assistant.steps.{fn_name}",
                        "args_preview": args_preview,
                        "status": "running",
                        "result_preview": "",
                    }
                    yield sse_event("step", step_entry)

                    # Execute
                    try:
                        result = self._execute_tool(fn_name, parsed_args, message_obj=asst_msg)
                    except Exception as exc:
                        logger.exception("Error executing tool %s: %s", fn_name, exc)
                        result = {"error": "tool_error", "message": str(exc)}

                    # Format preview
                    if "error" in result:
                        step_entry["status"] = "failed"
                        step_entry["result_preview"] = result.get("message", "Error")
                    else:
                        step_entry["status"] = "done"
                        if fn_name == "search_customers":
                            cnt = result.get("count", 0)
                            step_entry["result_preview"] = f"Found {cnt} customer(s)"
                        elif fn_name == "search_items":
                            cnt = result.get("count", 0)
                            step_entry["result_preview"] = f"Found {cnt} item(s)"
                        elif fn_name == "get_stock":
                            step_entry["result_preview"] = f"{result.get('total_actual_qty', 0)} in stock"
                        elif fn_name == "get_rate":
                            step_entry["result_preview"] = f"Rate ₹{result.get('rate', '0.00')}"
                        elif "proposal" in result.get("type", ""):
                            step_entry["result_preview"] = "Proposal prepared"
                        else:
                            step_entry["result_preview"] = "Completed"

                    yield sse_event("step", step_entry)
                    steps_trace.append(step_entry)

                    # Check for special control events
                    if result.get("type") == "question":
                        yield sse_event("question", {
                            "text": result.get("question"),
                            "options": result.get("options", []),
                        })

                    if result.get("type") == "proposal":
                        last_proposal = result
                        yield sse_event("proposal", result)

                    if result.get("type") == "navigate":
                        yield sse_event("navigate", result)

                    # Append tool result to messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", f"call_{round_idx}"),
                        "name": fn_name,
                        "content": json.dumps(result, default=str),
                    })

            else:
                # Loop exhausted
                if not final_reply:
                    final_reply = LOOP_EXHAUSTED_REPLY
                    yield sse_event("reply", {"text": final_reply})

        except Exception as exc:
            logger.exception("Unexpected error in agent stream: %s", exc)
            final_reply = FALLBACK_REPLY
            yield sse_event("reply", {"text": final_reply})
            yield sse_event("error", {"code": "internal_error", "message": str(exc)})

        # Update assistant message in DB
        asst_msg.content = final_reply
        asst_msg.steps = steps_trace
        asst_msg.save(update_fields=["content", "steps"])

        if conversation.title == "" and user_text:
            conversation.title = (user_text[:75] + "...") if len(user_text) > 75 else user_text
            conversation.save(update_fields=["title", "updated_at"])

        yield sse_event("done", {
            "conversation_id": conversation.pk,
            "message_id": asst_msg.pk,
            "proposal": last_proposal,
        })
