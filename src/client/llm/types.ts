/**
 * Provider-agnostic LLM types for the agent loop. The loop speaks these
 * types only — OpenAI is one implementation, StubLLM another — so tests
 * and offline runs never depend on a live provider.
 */

export interface ToolCallDef {
  id: string;
  name: string;
  /** Raw JSON string, exactly as the model produced it. */
  arguments: string;
}

export interface OpenAIToolDef {
  type: "function";
  function: {
    name: string;
    description?: string;
    parameters: Record<string, unknown>;
  };
}

export type AgentMessage =
  | { role: "system" | "user"; content: string }
  | { role: "assistant"; content: string | null; toolCalls?: ToolCallDef[] }
  | { role: "tool"; toolCallId: string; name: string; content: string };

export interface LLMResponse {
  content: string | null;
  toolCalls: ToolCallDef[];
}

export interface LLMClient {
  readonly name: string;
  complete(messages: AgentMessage[], tools: OpenAIToolDef[]): Promise<LLMResponse>;
}
