import type { AppConfig } from "../../shared/config.js";
import { OpenAILLM } from "./openai-llm.js";
import { StubLLM } from "./stub-llm.js";
import type { LLMClient } from "./types.js";

/**
 * LLM selection. `auto` → OpenAI when a key exists, else the deterministic
 * stub — the agent is fully runnable offline either way.
 */
export function createLLM(config: AppConfig): LLMClient {
  switch (config.LLM_PROVIDER) {
    case "openai":
      if (!config.OPENAI_API_KEY) {
        throw new Error("LLM_PROVIDER=openai requires OPENAI_API_KEY");
      }
      return new OpenAILLM(config.OPENAI_API_KEY, config.CHAT_MODEL_NAME);
    case "stub":
      return new StubLLM();
    case "auto":
      return config.OPENAI_API_KEY
        ? new OpenAILLM(config.OPENAI_API_KEY, config.CHAT_MODEL_NAME)
        : new StubLLM();
  }
}
