// Run: node widget.test.mjs
import assert from "node:assert";
import { escapeHtml, formatAssistantText } from "./widget.js";

assert.equal(escapeHtml("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;");
assert.equal(escapeHtml(`it's "quoted" & <b>`), "it&#39;s &quot;quoted&quot; &amp; &lt;b&gt;");
assert.equal(formatAssistantText("**bold** and `code`"), "<strong>bold</strong> and <code>code</code>");
assert.equal(formatAssistantText("line1\nline2"), "line1\nline2");
assert.ok(formatAssistantText("```\nconst x = 1;\n```").includes("<pre><code>const x = 1;</code></pre>"));
assert.equal(formatAssistantText("<img onerror=alert(1)>"), "&lt;img onerror=alert(1)&gt;");

console.log("widget format self-check: OK");
