import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import App from './App';

describe('Frontend Contract Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders required initial elements and data-testids', () => {
    render(<App />);

    expect(screen.getByTestId('chat-input')).toBeInTheDocument();
    expect(screen.getByTestId('send-button')).toBeInTheDocument();
    expect(screen.getByTestId('system-prompt-input')).toBeInTheDocument();
  });

  it('sends message, clears chat-input, and renders assistant-message with exact metadata testids', async () => {
    const mockApiResponse = {
      session_id: 's-test-session',
      reply: 'The capital of France is Paris.',
      metadata: {
        resolution_source: 'LIVE_INFERENCE',
        model: 'gemini/gemini-2.0-flash',
        latency_ms: 812,
        input_tokens: 42,
        output_tokens: 9,
        key_id: 'key_1',
        fallback_attempts: 0
      }
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => mockApiResponse
    });

    render(<App />);

    const chatInput = screen.getByTestId('chat-input');
    const sendButton = screen.getByTestId('send-button');

    fireEvent.change(chatInput, { target: { value: 'What is the capital of France?' } });
    expect(chatInput.value).toBe('What is the capital of France?');

    fireEvent.click(sendButton);

    // chat-input must be EMPTY after sending
    expect(chatInput.value).toBe('');

    // Wait for assistant reply to appear
    await waitFor(() => {
      expect(screen.getByTestId('assistant-message')).toBeInTheDocument();
    });

    // Verify all meta testids inside assistant-message have EXACT text content
    const assistantMessage = screen.getByTestId('assistant-message');
    expect(assistantMessage).toHaveTextContent('The capital of France is Paris.');

    const metaSource = screen.getByTestId('meta-source');
    expect(metaSource.textContent.trim()).toBe('LIVE_INFERENCE');

    const metaModel = screen.getByTestId('meta-model');
    expect(metaModel.textContent.trim()).toBe('gemini/gemini-2.0-flash');

    const metaLatency = screen.getByTestId('meta-latency');
    expect(metaLatency.textContent.trim()).toBe('812 ms');

    const metaInputTokens = screen.getByTestId('meta-input-tokens');
    expect(metaInputTokens.textContent.trim()).toBe('42');

    const metaOutputTokens = screen.getByTestId('meta-output-tokens');
    expect(metaOutputTokens.textContent.trim()).toBe('9');
  });

  it('renders LLM_CACHE accurately on cache hit response', async () => {
    const mockCacheResponse = {
      session_id: 's-test-session',
      reply: 'The capital of France is Paris.',
      metadata: {
        resolution_source: 'LLM_CACHE',
        model: 'gemini/gemini-2.0-flash',
        latency_ms: 3,
        input_tokens: 42,
        output_tokens: 9,
        key_id: null,
        fallback_attempts: 0
      }
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => mockCacheResponse
    });

    render(<App />);

    const chatInput = screen.getByTestId('chat-input');
    fireEvent.change(chatInput, { target: { value: 'What is the capital of France?' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('meta-source').textContent.trim()).toBe('LLM_CACHE');
      expect(screen.getByTestId('meta-latency').textContent.trim()).toBe('3 ms');
    });
  });

  it('renders error message in chat with error-message testid on failure', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'all providers unavailable' })
    });

    render(<App />);

    const chatInput = screen.getByTestId('chat-input');
    fireEvent.change(chatInput, { target: { value: 'Fail test' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      const errorMsg = screen.getByTestId('error-message');
      expect(errorMsg).toBeInTheDocument();
      expect(errorMsg).toHaveTextContent('all providers unavailable');
    });

    // Make sure assistant-message is NOT rendered for errors
    expect(screen.queryByTestId('assistant-message')).not.toBeInTheDocument();
  });
});
