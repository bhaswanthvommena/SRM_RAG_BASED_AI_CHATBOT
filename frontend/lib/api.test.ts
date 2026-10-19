import assert from "node:assert/strict";
import test from "node:test";
import { applySseEvent, ChatStreamError } from "./api.ts";

function handlers() {
  const calls: string[] = [];
  return {
    calls,
    onMeta: () => calls.push("meta"),
    onToken: (text: string) => calls.push(`token:${text}`),
    onDone: () => calls.push("done"),
  };
}

test("SSE error event propagates with the request id", () => {
  const opts = handlers();
  assert.throws(
    () =>
      applySseEvent(
        "error",
        JSON.stringify({
          message: "The chat model is temporarily busy.",
          request_id: "abc123",
          error: "model_unavailable",
        }),
        opts
      ),
    (err: unknown) => {
      assert.ok(err instanceof ChatStreamError);
      assert.equal(err.message, "The chat model is temporarily busy.");
      assert.equal(err.requestId, "abc123");
      assert.equal(err.category, "model_unavailable");
      return true;
    }
  );
  assert.deepEqual(opts.calls, []);
});

test("malformed SSE JSON is ignored", () => {
  const opts = handlers();
  applySseEvent("token", "{not-json", opts);
  assert.deepEqual(opts.calls, []);
});

test("token and done events still dispatch", () => {
  const opts = handlers();
  applySseEvent("token", JSON.stringify({ text: "Hello" }), opts);
  applySseEvent("done", JSON.stringify({ conversation_id: "c1" }), opts);
  assert.deepEqual(opts.calls, ["token:Hello", "done"]);
});
