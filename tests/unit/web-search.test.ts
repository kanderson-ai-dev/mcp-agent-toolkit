import { describe, expect, it, vi } from "vitest";
import type { ToolError } from "../../src/shared/result.js";
import { testConfig } from "../helpers.js";
import {
  createSearchProvider,
  DuckDuckGoProvider,
  StubSearchProvider,
  TavilyProvider,
} from "../../src/server/tools/web-search.js";

const DDG_HTML = `
<html><body>
<div class="result">
  <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc">Example Page</a>
  <a class="result__snippet">A snippet about the example.</a>
</div>
<div class="result">
  <a class="result__a" href="https://direct.example.org/">Direct Link</a>
  <a class="result__snippet">Second snippet.</a>
</div>
</body></html>`;

function fakeFetch(body: string, init?: { status?: number; capture?: (input: unknown, i: unknown) => void }) {
  return vi.fn((input: unknown, i: unknown): Promise<Response> => {
    init?.capture?.(input, i);
    return Promise.resolve(new Response(body, { status: init?.status ?? 200 }));
  });
}

describe("web_search providers", () => {
  it("DuckDuckGo parses results and unwraps redirect URLs", async () => {
    const fetcher = fakeFetch(DDG_HTML);
    const ddg = new DuckDuckGoProvider(fetcher);
    const hits = await ddg.search("mcp protocol", 5);
    expect(hits).toHaveLength(2);
    expect(hits[0]?.title).toBe("Example Page");
    expect(hits[0]?.url).toBe("https://example.com/page"); // uddg unwrapped
    expect(hits[0]?.snippet).toBe("A snippet about the example.");
    expect(hits[1]?.url).toBe("https://direct.example.org/");
  });

  it("Tavily maps the REST shape and sends the key via header", async () => {
    const seen: { auth?: string } = {};
    const fetcher = fakeFetch(
      JSON.stringify({
        results: [{ title: "T", url: "https://u", content: "C" }],
      }),
      {
        capture: (_input, i) => {
          seen.auth = new Headers((i as RequestInit).headers).get("authorization") ?? undefined;
        },
      },
    );
    const tavily = new TavilyProvider("tvly-test-key", fetcher);
    const hits = await tavily.search("q", 3);
    expect(hits).toEqual([{ title: "T", url: "https://u", snippet: "C" }]);
    expect(seen.auth).toBe("Bearer tvly-test-key");
  });

  it("providers surface HTTP failures as upstream ToolError", async () => {
    const failing = fakeFetch("boom", { status: 502 });
    await expect(new TavilyProvider("k", failing).search("q", 3)).rejects.toMatchObject({
      code: "upstream",
    } satisfies Partial<ToolError>);
    await expect(new DuckDuckGoProvider(failing).search("q", 3)).rejects.toMatchObject({
      code: "upstream",
    } satisfies Partial<ToolError>);
  });

  it("providers wrap network-level failures (fetch threw) as upstream", async () => {
    const throwing = vi.fn(() => Promise.reject(new Error("ECONNREFUSED")));
    await expect(new TavilyProvider("k", throwing).search("q", 3)).rejects.toMatchObject({
      code: "upstream",
    } satisfies Partial<ToolError>);
    await expect(new DuckDuckGoProvider(throwing).search("q", 3)).rejects.toMatchObject({
      code: "upstream",
    } satisfies Partial<ToolError>);
  });

  it("DuckDuckGo skips result blocks without links", async () => {
    const html = `<div class="result"><span class="result__snippet">orphan</span></div>
      <div class="result"><a class="result__a" href="">Empty href</a></div>`;
    const hits = await new DuckDuckGoProvider(fakeFetch(html)).search("q", 5);
    expect(hits).toHaveLength(0);
  });

  it("stub provider is deterministic and offline", async () => {
    const hits = await new StubSearchProvider().search("anything", 1);
    expect(hits).toHaveLength(1);
    expect(hits[0]?.url).toContain("example.com");
  });

  it("factory picks: key→tavily, keyless→duckduckgo, none→stub", () => {
    expect(
      createSearchProvider(testConfig({ SEARCH_PROVIDER: "auto", SEARCH_API_KEY: "k" })).name,
    ).toBe("tavily");
    expect(createSearchProvider(testConfig({ SEARCH_PROVIDER: "auto" })).name).toBe("duckduckgo");
    expect(createSearchProvider(testConfig({ SEARCH_PROVIDER: "none" })).name).toBe("none");
    expect(createSearchProvider(testConfig({ SEARCH_PROVIDER: "duckduckgo" })).name).toBe("duckduckgo");
    expect(() => createSearchProvider(testConfig({ SEARCH_PROVIDER: "tavily" }))).toThrowError(
      /SEARCH_API_KEY/,
    );
  });
});
