"""The assistant agent loop and streaming handler.

A bounded native-function-calling loop (max 6 tool rounds per turn).
The LLM is advisory only:
- Read tools execute scoped to request.user with RBAC checks.
- Zero write tools.
- Transaction creations go through proposal builders and are saved only by the human.
- Step-by-step progress is streamed as Server-Sent Events (SSE).

The completion seam is completion_fn(messages, allow_tools=True), returning
{"content": str, "tool_calls": list|None, "usage": dict}. Tests stub it.
"""

import inspect
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Generator, List, Optional
import urllib.request
import urllib.error

from django.utils import timezone

from .models import AssistantConversation, AssistantMessage
from .prompts import SYSTEM_PROMPT, failure_reply, turn_context
from .tools import READ_TOOLS, CONTROL_TOOLS
from .proposals import PROPOSAL_BUILDERS

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
LLM_TIMEOUT_SECONDS = int(os.getenv("ASSISTANT_LLM_TIMEOUT", "30"))
MAX_OUTPUT_TOKENS = int(os.getenv("ASSISTANT_MAX_OUTPUT_TOKENS", "1500"))
# A new round starts only if it could still finish inside this budget, so a turn
# always ends with a reply before the proxy gives up on the request. Keep
# LLM_TIMEOUT_SECONDS < TURN_BUDGET_SECONDS < the proxy timeout.
TURN_BUDGET_SECONDS = int(os.getenv("ASSISTANT_TURN_BUDGET_SECONDS", "75"))
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
HISTORY_MESSAGES = 10

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_customers",
            "description": "Search customers by name. Tolerates spelling mistakes.",
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
            "description": "Search suppliers by name. Tolerates spelling mistakes.",
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
            "description": "Search items by name or code. Tolerates spelling mistakes.",
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
                    "party_id": {"type": "string", "description": "Optional numeric party id from a search"},
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
            "name": "sales_summary",
            "description": "Invoiced sales total, invoice count and unpaid amount for a period.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today", "7d", "30d", "90d", "365d"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_items",
            "description": "Best-selling items by billed amount for a period.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today", "7d", "30d", "90d", "365d"]},
                    "limit": {"type": "integer", "description": "Rows to return (default 10, max 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pending_orders",
            "description": "Sales orders not yet fully dispatched, oldest first.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "money_to_collect",
            "description": "Open customer receivables grouped by customer, with overdue amounts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "as_on": {"type": "string", "description": "Date YYYY-MM-DD (default today)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bills_to_pay",
            "description": "Open supplier bills with due dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "as_on": {"type": "string", "description": "Date YYYY-MM-DD (default today)"},
                    "filter_type": {"type": "string", "enum": ["overdue", "week", "month"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expense_summary",
            "description": "Expenses and wages by category for a period.",
            "parameters": {
                "type": "object",
                "properties": {"period": {"type": "string", "enum": ["7d", "30d", "90d", "365d"]}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gst_position",
            "description": "GST output, input credit and net payable for one month.",
            "parameters": {
                "type": "object",
                "properties": {
                    "month": {"type": "string", "description": "Month YYYY-MM (default this month)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stock_stages",
            "description": "Stock per item by stage: unprocessed, in process, ready, and critical level.",
            "parameters": {"type": "object", "properties": {}},
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
            "name": "propose_sales_order",
            "description": "Prepare a Sales Order form for user review. Use when customer places an order, or user says 'order', 'sold', 'becha', 'likho'. Never writes to DB.",
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
                                "qty": {"type": "number", "description": "Quantity ordered"},
                                "rate": {"type": "number", "description": "Optional explicit rate per unit"},
                            },
                            "required": ["item", "qty"],
                        },
                        "description": "Line items ordered",
                    },
                    "transaction_date": {"type": "string", "description": "Optional transaction date YYYY-MM-DD"},
                    "delivery_date": {"type": "string", "description": "Optional delivery date YYYY-MM-DD"},
                },
                "required": ["customer_query", "items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_sales_invoice",
            "description": "Prepare a Sales Invoice form for user review. Only for explicit invoice or bill requests. Never writes to DB.",
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
            "description": "Prepare a Payment Entry form for user review. For money a customer paid us, use propose_receipt. Never writes to DB.",
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
    {
        "type": "function",
        "function": {
            "name": "propose_receipt",
            "description": "Prepare a receipt for user review: a customer paid money. The amount is allocated to the customer's open bills, oldest due first. Use for 'diye', 'mile', 'jama'. Never writes to DB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_query": {
                        "type": "string",
                        "description": "Customer name",
                    },
                    "amount": {"type": "number", "description": "Amount received"},
                    "mode_of_payment": {
                        "type": "string",
                        "description": "Cash, Bank, Cheque, UPI",
                    },
                    "posting_date": {
                        "type": "string",
                        "description": "Optional date YYYY-MM-DD",
                    },
                },
                "required": ["customer_query", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_expense",
            "description": "Prepare an expense bill for user review, such as electricity, rent or hamali. Not for stock purchases. Use paid_now when it was already paid. Never writes to DB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category_query": {
                        "type": "string",
                        "description": "Expense category name, e.g. Bijli",
                    },
                    "amount": {"type": "number", "description": "Bill amount"},
                    "supplier_query": {
                        "type": "string",
                        "description": "Optional supplier name",
                    },
                    "expense_date": {
                        "type": "string",
                        "description": "Optional bill date YYYY-MM-DD",
                    },
                    "bill_no": {
                        "type": "string",
                        "description": "Optional supplier bill number",
                    },
                    "paid_now": {
                        "type": "boolean",
                        "description": "True if the bill was paid now",
                    },
                    "mode_of_payment": {
                        "type": "string",
                        "description": "Cash, Bank, Cheque, UPI",
                    },
                },
                "required": ["category_query", "amount"],
            },
        },
    },
]


def sse_event(event: str, data: Dict[str, Any]) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def to_wire(messages: List[Dict[str, Any]], model: str) -> List[Dict[str, Any]]:
    """Mark the static prompt as a cache point for Claude models.

    Claude only caches where a request marks it, and only an unchanged prefix, so
    the per-turn context goes after the marker. Other providers cache by prefix on
    their own, and an unknown cache_control key can be rejected, so they get the
    plain list.
    """
    system = messages[0]["content"] if messages else ""
    if not model.startswith("anthropic/") or not system.startswith(SYSTEM_PROMPT):
        return messages
    blocks = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": system[len(SYSTEM_PROMPT):].lstrip("\n")},
    ]
    return [{**messages[0], "content": blocks}, *messages[1:]]


def _clock() -> float:
    """Monotonic seconds. A module function so tests can fake the passage of time."""
    return time.monotonic()


class Agent:
    """Orchestrates an assistant turn."""

    def __init__(self, user, completion_fn: Optional[Callable] = None, company=None):
        self.user = user
        self.company = company
        self.language = "en"
        self.completion_fn = completion_fn or self._default_llm_completion
        self.usage = {"input": 0, "output": 0, "cached": 0}

    def _default_llm_completion(
        self, messages: List[Dict[str, Any]], allow_tools: bool = True
    ) -> Dict[str, Any]:
        """Call an OpenAI-compatible chat endpoint (OpenRouter by default)."""
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("Assistant LLM disabled: LLM_API_KEY is not set.")
            return {"content": failure_reply("fallback", self.language), "tool_calls": None}

        model = os.getenv("ASSISTANT_LLM_MODEL") or DEFAULT_MODEL
        endpoint = os.getenv("ASSISTANT_LLM_ENDPOINT") or "https://openrouter.ai/api/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if "openrouter.ai" in endpoint:
            headers["HTTP-Referer"] = "https://erp.local"
            headers["X-Title"] = "ERP Assistant"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": to_wire(messages, model),
            "temperature": 0.1,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        if allow_tools:
            payload["tools"] = TOOL_DEFINITIONS
            payload["tool_choice"] = "auto"

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            logger.warning("LLM API returned %s: %s", exc.code, exc.read()[:300])
            return {"content": failure_reply("fallback", self.language), "tool_calls": None}
        except Exception as exc:
            logger.warning("LLM API call failed: %s", exc)
            return {"content": failure_reply("fallback", self.language), "tool_calls": None}

        choices = result.get("choices") or []
        if not choices:
            logger.warning("LLM API returned no choices: %s", str(result)[:300])
            return {"content": failure_reply("fallback", self.language), "tool_calls": None}

        choice = choices[0].get("message", {})
        return {
            "content": choice.get("content") or "",
            "tool_calls": choice.get("tool_calls"),
            "usage": result.get("usage") or {},
        }

    def _execute_tool(self, name: str, args: Dict[str, Any], message_obj: Optional[AssistantMessage] = None) -> Dict[str, Any]:
        """Execute a tool strictly checking permissions.

        The model chooses only declared arguments: anything else it sends is dropped,
        and the company always comes from the request, never from the model.
        """
        if name in READ_TOOLS:
            fn, extra = READ_TOOLS[name], {}
        elif name in CONTROL_TOOLS:
            return CONTROL_TOOLS[name](**self._declared_args(CONTROL_TOOLS[name], args))
        elif name in PROPOSAL_BUILDERS:
            fn, extra = PROPOSAL_BUILDERS[name], {"message": message_obj}
        else:
            return {"error": "unknown_tool", "message": f"Tool '{name}' is not registered."}

        params = inspect.signature(fn).parameters
        if "company" in params and self.company is not None:
            extra["company"] = self.company.pk
        return fn(self.user, **self._declared_args(fn, args), **extra)

    @staticmethod
    def _declared_args(fn: Callable, args: Dict[str, Any]) -> Dict[str, Any]:
        hidden = {"user", "company", "message"}
        params = inspect.signature(fn).parameters
        return {k: v for k, v in args.items() if k in params and k not in hidden}

    def _add_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        usage = usage or {}
        self.usage["input"] += usage.get("prompt_tokens") or 0
        self.usage["output"] += usage.get("completion_tokens") or 0
        details = usage.get("prompt_tokens_details") or {}
        self.usage["cached"] += details.get("cached_tokens") or 0

    def run_stream(
        self,
        user_text: str,
        conversation: AssistantConversation,
        input_mode: str = "text",
        language: str = "en",
    ) -> Generator[str, None, None]:
        """Generator running the agent loop and yielding SSE events."""
        self.language = language
        started = _clock()
        # 1. Save user turn
        AssistantMessage.objects.create(
            conversation=conversation,
            role="user",
            content=user_text,
            input_mode=input_mode,
        )

        yield sse_event("meta", {"conversation_id": conversation.pk})

        # 2. Build history
        history_msgs = []
        recent = conversation.messages.order_by("-created_at")[:HISTORY_MESSAGES]
        for m in reversed(recent):
            if m.role in ["user", "assistant"] and m.content:
                history_msgs.append({"role": m.role, "content": m.content})

        context = turn_context(language, timezone.localdate(), getattr(self.company, "name", None))
        messages = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n{context}"},
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
                # The first round always runs; a later one only if it can finish in time.
                if round_idx and _clock() - started + LLM_TIMEOUT_SECONDS > TURN_BUDGET_SECONDS:
                    logger.warning("Assistant turn out of time after %d round(s).", round_idx)
                    final_reply = failure_reply("slow", language)
                    yield sse_event("reply", {"text": final_reply})
                    break

                # After a proposal the model only confirms it: no second proposal.
                llm_out = self.completion_fn(messages, allow_tools=last_proposal is None)
                self._add_usage(llm_out.get("usage"))
                content = llm_out.get("content") or ""
                tool_calls = llm_out.get("tool_calls") or []

                if not tool_calls:
                    final_reply = content or failure_reply("fallback", language)
                    yield sse_event("reply", {"text": final_reply})
                    break

                # Handle tool calls
                asst_turn_msg = {"role": "assistant", "content": content, "tool_calls": tool_calls}
                messages.append(asst_turn_msg)

                question = None
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
                        result = {"error": "tool_error", "message": "That lookup failed."}

                    # Format preview
                    if "error" in result:
                        step_entry["status"] = "failed"
                        step_entry["result_preview"] = result.get("message", "Error")
                    else:
                        step_entry["status"] = "done"
                        if fn_name == "search_customers":
                            cnt = result.get("count", 0)
                            step_entry["result_preview"] = str(cnt)
                        elif fn_name == "search_items":
                            cnt = result.get("count", 0)
                            step_entry["result_preview"] = str(cnt)
                        elif fn_name == "get_stock":
                            step_entry["result_preview"] = str(result.get("total_actual_qty", 0))
                        elif fn_name == "get_rate":
                            step_entry["result_preview"] = f"₹{result.get('rate', '0.00')}"
                        else:
                            step_entry["result_preview"] = ""

                    yield sse_event("step", step_entry)
                    steps_trace.append(step_entry)

                    # Check for special control events
                    if result.get("type") == "question":
                        question = question or {
                            "text": result.get("question"),
                            "options": result.get("options", []),
                        }

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

                # A question ends the turn: the user's answer is the next turn. It is
                # kept as the reply so that turn's history shows what was asked.
                if question:
                    final_reply = question["text"]
                    yield sse_event("question", question)
                    break

            else:
                # Loop exhausted
                if not final_reply:
                    final_reply = failure_reply("loop", language)
                    yield sse_event("reply", {"text": final_reply})

        except Exception as exc:
            logger.exception("Unexpected error in agent stream: %s", exc)
            final_reply = failure_reply("fallback", language)
            yield sse_event("reply", {"text": final_reply})
            yield sse_event("error", {"code": "internal_error"})

        logger.info(
            "assistant_usage model=%s input=%d cached=%d output=%d seconds=%.1f",
            os.getenv("ASSISTANT_LLM_MODEL") or DEFAULT_MODEL,
            self.usage["input"],
            self.usage["cached"],
            self.usage["output"],
            _clock() - started,
        )

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
