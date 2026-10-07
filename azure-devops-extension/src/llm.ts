// Model providers for the chat panel. A conversation keeps its own history in
// the provider's native shape, so the panel only deals in text.
//
// The calls go straight from the user's browser to the provider with the
// user's own API key. Nothing is proxied through Azure DevOps.

import Anthropic from "@anthropic-ai/sdk";

import type { ModelSettings } from "./chat-model";

export interface Conversation {
  /** Send a user message; `onText` receives the answer as it arrives. */
  send(text: string, onText: (delta: string) => void): Promise<string>;
}

export function startConversation(settings: ModelSettings, system: string): Conversation {
  return settings.provider === "anthropic" ? claudeConversation(settings, system) : openAiCompatibleConversation(settings, system);
}

function claudeConversation(settings: ModelSettings, system: string): Conversation {
  // The key belongs to the person at the keyboard and is used only from their
  // own browser, which is the case this switch exists for.
  const client = new Anthropic({ apiKey: settings.apiKey, dangerouslyAllowBrowser: true });
  const messages: Anthropic.Beta.BetaMessageParam[] = [];

  return {
    async send(text, onText) {
      messages.push({ role: "user", content: text });
      try {
        const stream = client.beta.messages.stream({
          model: settings.model,
          max_tokens: 64000,
          system,
          messages,
          // If the model declines a request, let the API retry it on a
          // fallback model instead of ending the conversation.
          betas: ["server-side-fallback-2026-07-01"],
          fallbacks: "default",
        });
        stream.on("text", onText);
        const message = await stream.finalMessage();
        // Keep every block, not just the text: thinking blocks have to be
        // sent back unchanged on the next turn.
        messages.push({ role: "assistant", content: message.content as Anthropic.Beta.BetaContentBlockParam[] });
        if (message.stop_reason === "refusal") {
          throw new Error("The model declined this request.");
        }
        const answer = message.content.flatMap((block) => (block.type === "text" ? [block.text] : [])).join("");
        if (message.stop_reason === "max_tokens") {
          throw new Error("The answer was cut off because it became too long. Ask for a smaller proposal.");
        }
        return answer;
      } catch (error) {
        // Drop the unanswered question so the next attempt starts clean.
        if (messages[messages.length - 1]?.role === "user") {
          messages.pop();
        }
        throw new Error(describeClaudeError(error));
      }
    },
  };
}

function describeClaudeError(error: unknown): string {
  if (error instanceof Anthropic.AuthenticationError) {
    return "The API key was rejected. Check it in the model settings.";
  }
  if (error instanceof Anthropic.PermissionDeniedError) {
    return "This API key is not allowed to use that model.";
  }
  if (error instanceof Anthropic.NotFoundError) {
    return "The model name was not found. Check it in the model settings.";
  }
  if (error instanceof Anthropic.RateLimitError) {
    return "The provider's rate limit was reached. Wait a moment and try again.";
  }
  if (error instanceof Anthropic.APIConnectionError) {
    return "Could not reach the model provider from this browser. A network policy or browser extension may be blocking it.";
  }
  if (error instanceof Anthropic.APIError) {
    return `The model provider returned an error (${error.status ?? "unknown"}): ${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}

interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

/** Any provider that speaks the OpenAI chat completions protocol. */
function openAiCompatibleConversation(settings: ModelSettings, system: string): Conversation {
  const messages: ChatMessage[] = [{ role: "system", content: system }];

  return {
    async send(text, onText) {
      messages.push({ role: "user", content: text });
      let response: Response;
      try {
        response = await fetch(`${settings.baseUrl}/chat/completions`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${settings.apiKey}` },
          body: JSON.stringify({ model: settings.model, messages }),
        });
      } catch {
        messages.pop();
        throw new Error("Could not reach the model provider from this browser. It may not allow calls from a web page, or a network policy is blocking it.");
      }
      if (!response.ok) {
        messages.pop();
        const detail = await response.text().catch(() => "");
        throw new Error(
          response.status === 401
            ? "The API key was rejected. Check it in the model settings."
            : `The model provider returned an error (${response.status}): ${detail.slice(0, 300)}`,
        );
      }
      const data = (await response.json()) as { choices?: { message?: { content?: string } }[] };
      const answer = data.choices?.[0]?.message?.content ?? "";
      if (!answer) {
        messages.pop();
        throw new Error("The model provider returned an empty answer.");
      }
      messages.push({ role: "assistant", content: answer });
      onText(answer);
      return answer;
    },
  };
}
