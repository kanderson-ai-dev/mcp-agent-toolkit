import OpenAI from "openai";
import type {
  AgentMessage,
  LLMClient,
  LLMResponse,
  OpenAIToolDef,
  ToolCallDef,
} from "./types.js";

/**
 * Real LLM over `openai` chat completions. Only used when
 * `OPENAI_API_KEY` is present — otherwise the agent runs on StubLLM.
 */
export class OpenAILLM implements LLMClient {
  readonly name = "openai";
  private readonly client: OpenAI;

  constructor(
    apiKey: string,
    private readonly model: string,
    client?: OpenAI,
  ) {
    this.client = client ?? new OpenAI({ apiKey, maxRetries: 2, timeout: 60_000 });
  }

  async complete(messages: AgentMessage[], tools: OpenAIToolDef[]): Promise<LLMResponse> {
    const res = await this.client.chat.completions.create({
      model: this.model,
      messages: toOpenAIMessages(messages),
      tools,
      tool_choice: "auto",
    });
    const msg = res.choices[0]?.message;
    const toolCalls: ToolCallDef[] = (msg?.tool_calls ?? [])
      .filter((t) => t.type === "function")
      .map((t) => ({
        id: t.id,
        name: t.function.name,
        arguments: t.function.arguments,
      }));
    return { content: msg?.content ?? null, toolCalls };
  }
}

function toOpenAIMessages(messages: AgentMessage[]): OpenAI.ChatCompletionMessageParam[] {
  return messages.map((m): OpenAI.ChatCompletionMessageParam => {
    switch (m.role) {
      case "system":
      case "user":
        return { role: m.role, content: m.content };
      case "assistant":
        return {
          role: "assistant",
          content: m.content,
          ...(m.toolCalls && m.toolCalls.length > 0
            ? {
                tool_calls: m.toolCalls.map((t) => ({
                  id: t.id,
                  type: "function" as const,
                  function: { name: t.name, arguments: t.arguments },
                })),
              }
            : {}),
        };
      case "tool":
        return { role: "tool", tool_call_id: m.toolCallId, content: m.content };
    }
  });
}
