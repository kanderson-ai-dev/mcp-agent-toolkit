import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { TechnicalDetails } from "./TechnicalDetails";

const PAYLOAD = { tool: "db_query", args: { query: "SELECT 1" } };
const EXPECTED_JSON = JSON.stringify(PAYLOAD, null, 2);

describe("TechnicalDetails", () => {
  it("is collapsed by default — no raw JSON visible", () => {
    render(<TechnicalDetails payload={PAYLOAD} />);
    const toggle = screen.getByRole("button", { name: /view raw json/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/"db_query"/)).not.toBeInTheDocument();
  });

  it("shows the pretty JSON only after expanding", async () => {
    const user = userEvent.setup();
    render(<TechnicalDetails payload={PAYLOAD} />);
    await user.click(screen.getByRole("button", { name: /view raw json/i }));
    const pre = document.querySelector("pre");
    expect(pre?.textContent).toBe(EXPECTED_JSON);
  });

  it("copy button writes the exact JSON to the clipboard", async () => {
    // userEvent.setup() stubs navigator.clipboard with a working store.
    const user = userEvent.setup();
    render(<TechnicalDetails payload={PAYLOAD} />);
    await user.click(screen.getByRole("button", { name: /view raw json/i }));
    await user.click(screen.getByRole("button", { name: /copy json/i }));
    expect(await navigator.clipboard.readText()).toBe(EXPECTED_JSON);
  });
});
