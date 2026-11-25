import React, { useState, useEffect, useRef } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function generateSessionId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `s-${crypto.randomUUID()}`;
  }
  return `s-${Math.random().toString(36).substring(2, 11)}`;
}

export default function App() {
  const [sessionId] = useState(generateSessionId);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [messages, setMessages] = useState([]);
  const [inputText, setInputText] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  const handleSend = async (e) => {
    if (e) e.preventDefault();
    const trimmed = inputText.trim();
    if (!trimmed || loading) return;

    // Immediately clear input as required
    setInputText('');

    // Append user message to UI
    const userMsg = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed
    };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const payload = {
        session_id: sessionId,
        message: trimmed
      };
      if (systemPrompt.trim()) {
        payload.system_prompt = systemPrompt.trim();
      }

      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      });

      const data = await res.json();

      if (!res.ok) {
        let errorMsg = 'Failed to send message';
        if (data && data.detail) {
          if (typeof data.detail === 'string') {
            errorMsg = data.detail;
          } else if (Array.isArray(data.detail)) {
            errorMsg = data.detail.map((d) => d.msg || JSON.stringify(d)).join(', ');
          } else {
            errorMsg = JSON.stringify(data.detail);
          }
        }
        setMessages((prev) => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            role: 'error',
            content: `Error (${res.status}): ${errorMsg}`
          }
        ]);
      } else {
        const assistantMsg = {
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: data.reply,
          metadata: data.metadata
        };
        setMessages((prev) => [...prev, assistantMsg]);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          role: 'error',
          content: `Network connection error: ${err.message || 'Unable to connect to backend'}`
        }
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="app-container">
      {/* Header */}
      <header className="app-header">
        <div className="header-brand">
          <span className="brand-icon">⚡</span>
          <div>
            <h1 className="brand-title">KeyRelay</h1>
          </div>
        </div>
        <div className="header-badges">
          <span className="badge badge-session" title="Active session identifier">
            Session: {sessionId.substring(0, 10)}...
          </span>
          <span className="badge badge-status" title="Active backend target">
            {API_BASE}
          </span>
        </div>
      </header>

      {/* System Prompt Bar */}
      <section className="system-prompt-bar">
        <label htmlFor="system-prompt-input" className="system-prompt-label">
          Optional Session System Prompt
        </label>
        <input
          id="system-prompt-input"
          data-testid="system-prompt-input"
          type="text"
          className="system-prompt-input"
          placeholder="You are a helpful and concise AI assistant..."
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          disabled={loading}
        />
      </section>

      {/* Chat Messages */}
      <main className="chat-window">
        {messages.length === 0 ? (
          <div className="chat-empty-state">
            <div className="empty-icon">🛡️</div>
            <h2 className="empty-title">Failover & Response Caching Ready</h2>
            <p className="empty-desc">
              Ask anything to test live inference. Identical queries will automatically hit the LiteLLM cache.
              Failover occurs seamlessly across the configured API key pool.
            </p>
          </div>
        ) : (
          messages.map((msg) => {
            if (msg.role === 'user') {
              return (
                <div key={msg.id} className="message-row user">
                  <div className="user-bubble">{msg.content}</div>
                </div>
              );
            }

            if (msg.role === 'error') {
              return (
                <div key={msg.id} className="message-row assistant">
                  <div className="error-bubble" data-testid="error-message">
                    <span>⚠️</span>
                    <span>{msg.content}</span>
                  </div>
                </div>
              );
            }

            // Assistant message with exact testids
            const meta = msg.metadata || {};
            const isCache = meta.resolution_source === 'LLM_CACHE';

            return (
              <div key={msg.id} className="message-row assistant">
                <div className="assistant-container" data-testid="assistant-message">
                  <div className="assistant-card">
                    <div className="assistant-text">{msg.content}</div>

                    {/* Metadata bar with strictly required data-testids containing only the values */}
                    <div className="metadata-grid">
                      <div className="meta-pill">
                        <span className="meta-label">Source:</span>
                        <span
                          data-testid="meta-source"
                          className={isCache ? 'meta-val-source-cache' : 'meta-val-source-live'}
                        >
                          {meta.resolution_source || 'LIVE_INFERENCE'}
                        </span>
                      </div>

                      <div className="meta-pill">
                        <span className="meta-label">Model:</span>
                        <span data-testid="meta-model" className="meta-val-model">
                          {meta.model || 'unknown'}
                        </span>
                      </div>

                      <div className="meta-pill">
                        <span className="meta-label">Latency:</span>
                        <span data-testid="meta-latency" className="meta-val-latency">
                          {meta.latency_ms !== undefined ? `${meta.latency_ms} ms` : '0 ms'}
                        </span>
                      </div>

                      <div className="meta-pill">
                        <span className="meta-label">Input tokens:</span>
                        <span data-testid="meta-input-tokens" className="meta-val-tokens">
                          {meta.input_tokens ?? 0}
                        </span>
                      </div>

                      <div className="meta-pill">
                        <span className="meta-label">Output tokens:</span>
                        <span data-testid="meta-output-tokens" className="meta-val-tokens">
                          {meta.output_tokens ?? 0}
                        </span>
                      </div>

                      {meta.key_id && (
                        <div className="meta-pill">
                          <span className="meta-label">Key:</span>
                          <span className="meta-val-key">{meta.key_id}</span>
                        </div>
                      )}

                      {meta.fallback_attempts !== undefined && meta.fallback_attempts > 0 && (
                        <div className="meta-pill">
                          <span className="meta-label">Fallbacks:</span>
                          <span className="meta-val-key">{meta.fallback_attempts}</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })
        )}

        {loading && (
          <div className="message-row assistant">
            <div className="typing-indicator">
              <span className="dot"></span>
              <span className="dot"></span>
              <span className="dot"></span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </main>

      {/* Input Form */}
      <footer className="chat-input-container">
        <textarea
          data-testid="chat-input"
          className="chat-textarea"
          placeholder="Ask a question or type a message... (Press Enter to send, Shift+Enter for newline)"
          rows="1"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
        />
        <button
          type="button"
          data-testid="send-button"
          className="send-button"
          onClick={handleSend}
          disabled={loading || !inputText.trim()}
        >
          {loading ? 'Sending...' : 'Send ➔'}
        </button>
      </footer>
    </div>
  );
}
