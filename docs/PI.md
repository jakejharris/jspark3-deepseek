# Optional Pi companion

Version: v2.0.1 · separate from core throughput qualification.

Core Tempo serves an OpenAI-compatible API and requires no Pi install, desktop or Library. Configure your existing Pi provider for the configured rank0 endpoint plus `/v1`, model ID `deepseek-v4.1-flash`, and the OpenAI completions API. Use your installed Pi version's provider configuration format; the template below contains only connection/capability values, not a loadable model definition:

```json
{
  "baseUrl": "http://192.0.2.11:8888/v1",
  "modelId": "deepseek-v4.1-flash",
  "input": ["text", "image"],
  "contextWindow": 300000,
  "maxTokens": 65536
}
```

These are configured limits, not maximum-context/output certification. The core default is thinking off. Enable reasoning deliberately per request through `chat_template_kwargs`; historical Pi integration mapped low→low, medium→high, high→xhigh, max→max. Minimal and xhigh had no explicit override. Check the outgoing request with a public fixture before assuming your Pi version preserves that mapping.

The server allows at most4 images per prompt and a1GiB multimodal processor cache. A separate native vision smoke should use fresh randomized text/color on an image, then verify that Pi forwards the actual attachment or tool-return image. Direct API vision success does not prove a browser/computer tool path. Existing desktop/session history must not be copied into this recipe or diagnostic bundle.

A browser/computer extension and shared Library are optional user-managed companions. Keep their paths and credentials in your own configuration; activate extensions through the installed Pi extension mechanism, then test the actual `/reload` behavior with a fresh fixture. This release contains no desktop image, private session portals, Library contents or user histories. Companion memory and desktop use are outside the historical L5-P throughput cohort.

The historical throughput qualification did not include a fresh vision test. Later local Pi/vision experiments are separate evidence, not retroactive throughput validation. Optional Pi/browser integration is not a requirement for the core installer gate.
