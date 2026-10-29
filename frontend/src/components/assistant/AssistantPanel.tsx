import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import {
  Loader2,
  Mic,
  MicOff,
  Send,
  Sparkles,
  X,
  PlusCircle,
  HelpCircle,
  ArrowRight,
} from "lucide-react";
import { apiClient } from "@/api/client";
import { useVoiceInput } from "@/hooks/useVoiceInput";
import { AgentWorkingOverlay, AgentStep } from "./AgentWorkingOverlay";

export interface MessageBubble {
  id: string;
  role: "user" | "assistant";
  content: string;
  questionOptions?: string[];
  proposal?: any;
}

interface AssistantPanelProps {
  isOpen: boolean;
  onClose: () => void;
  initialQuery?: string;
}

export const AssistantPanel: React.FC<AssistantPanelProps> = ({
  isOpen,
  onClose,
  initialQuery,
}) => {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();

  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<MessageBubble[]>([]);
  const [inputText, setInputText] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, steps]);

  const {
    supported: micSupported,
    recording,
    transcribing,
    error: voiceError,
    start: startVoice,
    stop: stopVoice,
  } = useVoiceInput(
    (transcript) => {
      if (transcript) {
        setInputText(transcript);
        void handleSend(transcript, "voice");
      }
    },
    () => i18n.language || "hi"
  );

  useEffect(() => {
    if (initialQuery && isOpen) {
      setInputText(initialQuery);
    }
  }, [initialQuery, isOpen]);

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);
  };

  const handleSend = async (textToSend?: string, inputMode: "text" | "voice" = "text") => {
    const text = (textToSend || inputText).trim();
    if (!text || isStreaming) return;

    setInputText("");
    setIsStreaming(true);
    setSteps([]);
    setStreamError(null);

    // Add user bubble
    const userMsgId = `user-${Date.now()}`;
    setMessages((prev) => [...prev, { id: userMsgId, role: "user", content: text }]);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const token = localStorage.getItem("access_token") || localStorage.getItem("token") || "";
      const response = await fetch("/api/assistant/chat/stream/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: token ? `Bearer ${token}` : "",
        },
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId,
          input_mode: inputMode,
          language: i18n.language || "en",
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Server returned HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response stream");

      const decoder = new TextDecoder();
      let buffer = "";
      let activeReply = "";
      let questionData: { text: string; options: string[] } | null = null;
      let proposalData: any = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";

        for (const rawEvent of events) {
          const lines = rawEvent.split("\n");
          let eventType = "message";
          let dataStr = "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              eventType = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              dataStr = line.slice(6).trim();
            }
          }

          if (!dataStr) continue;

          try {
            const data = JSON.parse(dataStr);
            if (eventType === "meta" && data.conversation_id) {
              setConversationId(data.conversation_id);
            } else if (eventType === "step") {
              setSteps((prev) => {
                const next = [...prev];
                const existingIdx = next.findIndex(
                  (s) => s.key === data.key && s.status === "running"
                );
                if (existingIdx !== -1) {
                  next[existingIdx] = data;
                } else {
                  next.push(data);
                }
                return next;
              });
            } else if (eventType === "question") {
              questionData = data;
            } else if (eventType === "proposal") {
              proposalData = data;
            } else if (eventType === "reply") {
              activeReply = data.text || "";
            } else if (eventType === "navigate") {
              if (data.route) {
                navigate(data.route);
                onClose();
              }
            } else if (eventType === "error") {
              setStreamError(data.message || t("assistant.errors.turn_failed"));
            } else if (eventType === "done") {
              if (data.proposal) {
                proposalData = data.proposal;
              }
            }
          } catch {
            // Ignore parse errors on partial chunks
          }
        }
      }

      // Add assistant response bubble
      if (activeReply || questionData || proposalData) {
        setMessages((prev) => [
          ...prev,
          {
            id: `asst-${Date.now()}`,
            role: "assistant",
            content: activeReply || (questionData ? questionData.text : "Proposal prepared:"),
            questionOptions: questionData?.options,
            proposal: proposalData,
          },
        ]);
      }

      // Handoff to prefilled document form if proposal was generated
      if (proposalData && proposalData.route) {
        setTimeout(() => {
          onClose();
          navigate(proposalData.route, {
            state: {
              initialValues: proposalData.values,
              assistant: {
                proposalId: proposalData.proposal_id,
                summary: proposalData.summary,
                aiFilledKeys: proposalData.ai_filled_keys,
                warnings: proposalData.warnings,
                target: proposalData.target,
              },
            },
          });
        }, 800);
      }
    } catch (err: any) {
      if (err.name !== "AbortError") {
        setStreamError(t("assistant.errors.turn_failed"));
        setMessages((prev) => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            role: "assistant",
            content: t("assistant.errors.turn_failed"),
          },
        ]);
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  };

  const handleOptionClick = (option: string) => {
    void handleSend(option, "text");
  };

  const startNewChat = () => {
    setConversationId(null);
    setMessages([]);
    setSteps([]);
  };

  if (!isOpen) return null;

  const isRTL = i18n.language === "ur";

  return (
    <>
      <div
        dir={isRTL ? "rtl" : "ltr"}
        className="fixed bottom-6 right-6 z-40 w-full max-w-lg h-[620px] max-h-[85vh] bg-slate-900 border border-slate-700/80 rounded-2xl shadow-2xl flex flex-col overflow-hidden animate-in slide-in-from-bottom-5 duration-200"
      >
        {/* Panel Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800 bg-slate-900/80 backdrop-blur-md">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-500/20 text-indigo-400 flex items-center justify-center">
              <Sparkles className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-slate-100">{t("assistant.title")}</h2>
              <p className="text-xs text-slate-400">{t("assistant.floating_label")}</p>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={startNewChat}
              className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition"
              title={t("assistant.new_chat")}
            >
              <PlusCircle className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Message Thread */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center p-6 text-slate-400 space-y-3">
              <Sparkles className="w-8 h-8 text-indigo-400/50" />
              <p className="text-sm">{t("assistant.no_messages")}</p>
              <div className="text-xs text-slate-500 space-y-1">
                <p>• "Sharma Traders ko 20 kilo haldi aur 10 kilo mirchi becha"</p>
                <p>• "Gupta Suppliers se 200 kilo dhaniya aaya"</p>
                <p>• "Kitna baaki hai Sharma ka?"</p>
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div
                key={m.id}
                className={`flex flex-col ${m.role === "user" ? "items-end" : "items-start"}`}
              >
                <div
                  className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                    m.role === "user"
                      ? "bg-indigo-600 text-white rounded-br-none"
                      : "bg-slate-800/90 text-slate-200 border border-slate-700/60 rounded-bl-none"
                  }`}
                >
                  <p className="whitespace-pre-wrap">{m.content}</p>

                  {/* Tappable question options */}
                  {m.questionOptions && m.questionOptions.length > 0 && (
                    <div className="mt-3 pt-2.5 border-t border-slate-700/60 flex flex-wrap gap-2">
                      {m.questionOptions.map((opt, oIdx) => (
                        <button
                          key={oIdx}
                          type="button"
                          onClick={() => handleOptionClick(opt)}
                          className="px-3 py-1.5 text-xs font-medium bg-indigo-500/20 hover:bg-indigo-500/30 text-indigo-300 border border-indigo-500/40 rounded-xl transition text-left"
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Proposal link chip */}
                  {m.proposal && (
                    <div className="mt-3 pt-2.5 border-t border-slate-700/60">
                      <button
                        type="button"
                        onClick={() => {
                          onClose();
                          navigate(m.proposal.route, {
                            state: {
                              initialValues: m.proposal.values,
                              assistant: {
                                proposalId: m.proposal.proposal_id,
                                summary: m.proposal.summary,
                                aiFilledKeys: m.proposal.ai_filled_keys,
                                warnings: m.proposal.warnings,
                                target: m.proposal.target,
                              },
                            },
                          });
                        }}
                        className="inline-flex items-center gap-1.5 px-3 py-1 text-xs font-semibold rounded-lg bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/30 transition"
                      >
                        <span>Open {m.proposal.target}</span>
                        <ArrowRight className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar */}
        <div className="p-3.5 border-t border-slate-800 bg-slate-900/90">
          {voiceError && (
            <p className="text-xs text-rose-400 mb-2 px-1">
              {t(`assistant.errors.${voiceError.replace("-", "_")}`)}
            </p>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void handleSend();
            }}
            className="flex items-center gap-2"
          >
            {micSupported && (
              <button
                type="button"
                onClick={recording ? stopVoice : startVoice}
                disabled={transcribing || isStreaming}
                className={`p-2.5 rounded-xl border transition shrink-0 ${
                  recording
                    ? "bg-rose-500 text-white border-rose-400 animate-pulse"
                    : transcribing
                    ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
                    : "bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700"
                }`}
                title={recording ? t("assistant.mic_stop") : t("assistant.mic_start")}
              >
                {recording ? (
                  <MicOff className="w-4 h-4" />
                ) : transcribing ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Mic className="w-4 h-4" />
                )}
              </button>
            )}

            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder={recording ? t("assistant.listening") : t("assistant.input_placeholder")}
              disabled={isStreaming || recording}
              className="flex-1 bg-slate-800/80 border border-slate-700 text-slate-100 placeholder-slate-500 text-sm px-3.5 py-2.5 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500/50"
            />

            <button
              type="submit"
              disabled={!inputText.trim() || isStreaming || recording}
              className="p-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:hover:bg-indigo-600 text-white transition shrink-0"
            >
              {isStreaming ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </button>
          </form>
        </div>
      </div>

      {/* Live Working Overlay */}
      <AgentWorkingOverlay
        isOpen={isStreaming}
        steps={steps}
        error={streamError}
        onStop={handleStop}
      />
    </>
  );
};
