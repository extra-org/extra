# Changelog

## [0.5.0](https://github.com/extra-org/extra/compare/agent-engine-v0.4.1...agent-engine-v0.5.0) (2026-08-04)


### Features

* expose manager playground route ([82c405f](https://github.com/extra-org/extra/commit/82c405fd4436675a2fed20fc687705e7e4b09611))


### Bug Fixes

* **api:** sanitize raw tool exception text to generic error in ToolRecord ([4959cfc](https://github.com/extra-org/extra/commit/4959cfcbd92b8064396570e121c96d06535b6f8b))
* **api:** sanitize raw tool exception text to generic error in ToolRecord ([24cd005](https://github.com/extra-org/extra/commit/24cd005a4ff88bed77ddcfd5a28c87d7472224f8))
* **cli:** validate config before serving ([b172b1a](https://github.com/extra-org/extra/commit/b172b1ab8f41cd8c6a1f03b473808c871ed4417d))
* **cli:** validate config before serving ([5f1442f](https://github.com/extra-org/extra/commit/5f1442f286a7bc6723c33b48a54f0267cf10a48d))
* **generate:** report unreferenced tools ([2581c0e](https://github.com/extra-org/extra/commit/2581c0e5e910de24287057f73f323d9a03338a4d))
* **generate:** report unreferenced tools ([cc80456](https://github.com/extra-org/extra/commit/cc804560ea3774d41df769977621e8a2f0953163))
* **logging:** align uvicorn/third-party logs with structured formatter ([90711ab](https://github.com/extra-org/extra/commit/90711ab77012aa05cf6eee0fb12d38f67c18fb5e))
* **logging:** make configure_logging own the root handler unconditionally ([861c60f](https://github.com/extra-org/extra/commit/861c60fca7c744b175bcb3d60f79b0fee8a50516))
* **parser:** reject unknown and misplaced YAML keys during spec validation ([016f89d](https://github.com/extra-org/extra/commit/016f89d2cc87849320c3f711495f870d1715925b))
* **parser:** reject unknown and misplaced YAML keys during spec validation ([c981c57](https://github.com/extra-org/extra/commit/c981c57a7a71a469180c3e575122b046f94348f1))
* **widget:** avoid crypto.randomUUID crash outside secure contexts ([46a98ce](https://github.com/extra-org/extra/commit/46a98ce118a99b9a249bef86c52aae484e95a8c9))
* **widget:** avoid crypto.randomUUID crash outside secure contexts ([da666bc](https://github.com/extra-org/extra/commit/da666bc004bdd013862c3d466e519cd111413a96))
* **widget:** hide internal tool provider labels ([5166956](https://github.com/extra-org/extra/commit/51669564e712adcc1406d754f8c8b0f652fe20f0))
* **widget:** hide tool provider labels ([3a5f3a2](https://github.com/extra-org/extra/commit/3a5f3a209d880ce8854995c0c417faac3089a218))


### Performance Improvements

* **parser:** optimize _validate_unknown_keys loop ([d821d6b](https://github.com/extra-org/extra/commit/d821d6b5620d476b722c4dcc2712f4635e0aec1f))

## [0.4.1](https://github.com/extra-org/extra/compare/agent-engine-v0.4.0...agent-engine-v0.4.1) (2026-08-01)


### Bug Fixes

* **lint:** import Any and format import blocks ([a897dc1](https://github.com/extra-org/extra/commit/a897dc15f6151b2963ab87b4e85fa2c01ef6e3d4))
* **widget:** show context-limit message on HTTP 429 token budget exceeded and disable composer ([e49e814](https://github.com/extra-org/extra/commit/e49e8144ca7990a4650b37d3c4157c7aa8694380))

## [0.4.0](https://github.com/extra-org/extra/compare/agent-engine-v0.3.1...agent-engine-v0.4.0) (2026-07-31)


### Features

* add agent auto mode config ([a1d1016](https://github.com/extra-org/extra/commit/a1d10165769e86b93d256d9c29d7ba90d65d5b20))
* add approval lifecycle primitives ([5a9422f](https://github.com/extra-org/extra/commit/5a9422ffa95576363b6a770da7d8ce991e05265f))
* add data class for hidm ([6b0d71a](https://github.com/extra-org/extra/commit/6b0d71ad462132f9517fa3443115dc09a1e5bf4b))
* add examples/starter — a small, complete reference system ([a27d072](https://github.com/extra-org/extra/commit/a27d072fc9fb4cc7d0a986e0ba8dcd8d06446646))
* add scoped session approval repository ([ad5d487](https://github.com/extra-org/extra/commit/ad5d48725df618da17b74199efe82fe38d2907be))
* **approvals:** propagate identity and grant metadata through HITL ([3f46464](https://github.com/extra-org/extra/commit/3f464641a9b2e74ab0a6da02467d936f83e831da))
* build first layer engine for hidm ([3fa3b49](https://github.com/extra-org/extra/commit/3fa3b49edcde02e2c3b410306ff2b35f5620cdf2))
* **cli:** handle tool approvals in local chat ([03e26de](https://github.com/extra-org/extra/commit/03e26deef2d33d3de60fac300fee1bc7b141f362))
* **cli:** share session approvals across application lifetime ([621d8c4](https://github.com/extra-org/extra/commit/621d8c415c72afee2cd6aa33950c8a0c4fe1d4c9))
* **composition:** manage session approval repository lifetime ([db5b0e3](https://github.com/extra-org/extra/commit/db5b0e367019a789a332e919b82a14f4c6034ec7))
* coordinate function that decide when to invoke tool or wait for approve ([be30aee](https://github.com/extra-org/extra/commit/be30aee9ea532bde9661e63f8335f1e1e2a50fcc))
* enable auto mode in enterprise knowledge example ([62c1b87](https://github.com/extra-org/extra/commit/62c1b8752767647acca0e3d85bcf5d33c4876407))
* **examples:** add deterministic approval demo ([a69ac63](https://github.com/extra-org/extra/commit/a69ac632c584a8dad2838a307f1d6aa0a281b8f5))
* **examples:** add real LLM local MCP approval flow ([854cae2](https://github.com/extra-org/extra/commit/854cae2099116d423af713f4c30c7c625168bc71))
* **examples:** demonstrate MCP session approvals ([4597748](https://github.com/extra-org/extra/commit/4597748406fb904bfe3428034cda81f8b077e267))
* execute tool after approve ([c690438](https://github.com/extra-org/extra/commit/c690438f8418a5ea113b05f1f0c472cbf3023c63))
* expose approval resume API ([e633f98](https://github.com/extra-org/extra/commit/e633f983e70cc1ad25707bee845750de57472eac))
* fail startup validation on declared-but-unimplemented plugin stubs ([0b6ee39](https://github.com/extra-org/extra/commit/0b6ee39e1c1438e66df4bfcb342ab1b79a65639a))
* one-command local dev stack for the flagship example ([b88ed5f](https://github.com/extra-org/extra/commit/b88ed5f171eea7647df54110056df61be4af576a))
* scaffold missing prompt files during generate and validate them at startup ([#18](https://github.com/extra-org/extra/issues/18)) ([cdefacb](https://github.com/extra-org/extra/commit/cdefacbd3dc9122e9dd7682775f9a9a56e098db9))
* startup validation fails on declared-but-unimplemented plugin stubs ([89355a1](https://github.com/extra-org/extra/commit/89355a1e24f507ee3214b23137f12e69c9761c32))
* support fallback models and automatic retries on LLM failure ([#25](https://github.com/extra-org/extra/issues/25)) ([cfce637](https://github.com/extra-org/extra/commit/cfce6376f53ec08491e8c9e7e9de314f64efc302))
* support fallback models and automatic retries on LLM failure ([#25](https://github.com/extra-org/extra/issues/25)) ([b6b5018](https://github.com/extra-org/extra/commit/b6b501830f8603661c7d7ea9218dbd9ca33d73ab))
* **widget:** context-usage meter backed by the agent manager ([160813d](https://github.com/extra-org/extra/commit/160813d26525582e8728821c2b87748b7554f95a))
* **widget:** context-usage meter backed by the agent manager ([2239cd5](https://github.com/extra-org/extra/commit/2239cd50108cf01ec895d2fe7264260516d86e40))
* **widget:** conversation thread list — list, new, switch, auto-title ([40175e1](https://github.com/extra-org/extra/commit/40175e118b38057b0b261433f7417fb71e4ba60e))
* **widget:** conversation thread list — list, new, switch, auto-title ([6a3d111](https://github.com/extra-org/extra/commit/6a3d111373bc66730672a38433d9cd776cf5e795))
* **widget:** fluent assistant-ui-style UI and internal refactor ([ee6ec09](https://github.com/extra-org/extra/commit/ee6ec091fb9d914514ddafaccd73eb45dcd9e3b8))
* **widget:** fluent assistant-ui-style UI and internal refactor ([5dde997](https://github.com/extra-org/extra/commit/5dde99746654cf661920b2aacb225010f6b884d9))
* wire approvals into langgraph runtime ([2b325ed](https://github.com/extra-org/extra/commit/2b325ede090da696a15ff86503524f35e079d4df))


### Bug Fixes

* **approvals:** harden interrupted tool resumption ([4da978b](https://github.com/extra-org/extra/commit/4da978bb529fb7f95dcd0709296da8c6398b36ab))
* bundle all built-in providers and optional integrations into base deps ([d2284bb](https://github.com/extra-org/extra/commit/d2284bb5629434ecd4ca8072abc46e930011e352))
* bundle all built-in providers and optional integrations into base deps ([c862e54](https://github.com/extra-org/extra/commit/c862e54f99f6d9b7c7daaff4d0faa8be01dc7044))
* change the pending object property to description ([9186999](https://github.com/extra-org/extra/commit/91869990102384f490b23ab36ae7831e94585d85))
* **deploy:** publish multi-arch container image and optimize Docker layer caching ([d1be5de](https://github.com/extra-org/extra/commit/d1be5dedeb5fd2b424b991c3797cac0a47a3ea49))
* **deploy:** revert Dockerfile layer caching optimization to fix build backend lookup ([73fb756](https://github.com/extra-org/extra/commit/73fb7566c9b519d06f3783b15634a447bd2e9372))
* **generate:** derive the plugin package from import_roots, not the folder name ([56f24f3](https://github.com/extra-org/extra/commit/56f24f3131ea9026bfd0d3519863611725a0ca12))
* **generate:** derive the plugin package from import_roots, not the folder name ([50e1db9](https://github.com/extra-org/extra/commit/50e1db9a78b69d8352b593139c46b70bacfe35fb))
* GHSA-f4xh-w4cj-qxq8 security vulnerability ([3529eaf](https://github.com/extra-org/extra/commit/3529eaf9cded81fb23d12a2aa657f59e3dae6b33))
* **hooks:** log only meaningful hook actions ([25b25f2](https://github.com/extra-org/extra/commit/25b25f2026c6c598451a1263cb7a8e0f2858db35))
* make hidm docs simple ([2fda83d](https://github.com/extra-org/extra/commit/2fda83d586a9f156eba2fc57de33948258818318))
* **orchestrator:** load both system and orchestrator prompt files ([65b2812](https://github.com/extra-org/extra/commit/65b281234e8d6e11e35cc4631b7c9fd4a1355c4f))
* **orchestrator:** load both system and orchestrator prompt files ([677cca2](https://github.com/extra-org/extra/commit/677cca231f493ce320887c5779b426c33b8d0623)), closes [#38](https://github.com/extra-org/extra/issues/38)
* **orchestrator:** load both system and orchestrator prompt files ([8b1eec5](https://github.com/extra-org/extra/commit/8b1eec54a19b2150c442050f05c09f5e42b7626d)), closes [#38](https://github.com/extra-org/extra/issues/38)
* **persistence:** make create_session insert atomically ([506d21d](https://github.com/extra-org/extra/commit/506d21dfd685a9d3037d39914b232573ab49d858))
* **persistence:** write nothing when the conversation id is taken ([144cb4c](https://github.com/extra-org/extra/commit/144cb4c2b8bf5c35ccadf3ce9b034518cb76ec5a))
* publish multi-arch container image (linux/amd64 + linux/arm64) — closes [#37](https://github.com/extra-org/extra/issues/37) ([cbd37c0](https://github.com/extra-org/extra/commit/cbd37c039aa1f58b6af0cf6f503839dd33d4b7a3))
* resolve pre-existing lint and mypy errors so CI lands green ([7976bdc](https://github.com/extra-org/extra/commit/7976bdc3cb8b81a0070b2131a0ce6347e8a976e4))
* **runtime:** pass structured conversation history ([19f8281](https://github.com/extra-org/extra/commit/19f8281e36ebba7dfaef70adf8243c4de200d466))
* support backward compatible for auto mode ([cb4b695](https://github.com/extra-org/extra/commit/cb4b69548b548af2455b2b8874c9666f773f2eb6))
* **test:** repair the header name in the conversation listing test ([2f44394](https://github.com/extra-org/extra/commit/2f44394382d087e8510ddf9cbd6a545702797e8d))
* upgrade langsmith to &gt;=0.8.18 to address GHSA-f4xh-w4cj-qxq8 ([9ba25f8](https://github.com/extra-org/extra/commit/9ba25f89f13507d7d49b0982a884ed9243c81163))
* upgrade langsmith to 0.8.18 (GHSA-f4xh-w4cj-qxq8) ([3399cfd](https://github.com/extra-org/extra/commit/3399cfd1c063ca652e0330976d7bcd312b58e41f))
* use typing_extensions.TypedDict in local MCP tools for Python 3.11 ([98cd6c1](https://github.com/extra-org/extra/commit/98cd6c12caef2289d8a1c3f4b7d302f22171e3aa))
* **widget:** authorize conversations against the caller, not the id ([3aa5b40](https://github.com/extra-org/extra/commit/3aa5b401b746ec9cf4ae638066935596dbed0d2f))
* **widget:** close the remaining ownership-write and identity edges ([ea80caf](https://github.com/extra-org/extra/commit/ea80cafe2f82497ca2439914d8c24820d0bec00f))
* **widget:** derive turn identity from the session, scope storage per user ([f074134](https://github.com/extra-org/extra/commit/f074134a5e0acb27f9962b07ce9227d01b3a2c99))
* **widget:** fix the owner of a conversation at creation ([eb998d2](https://github.com/extra-org/extra/commit/eb998d2848f2684294897e4e902fbaeea48e77fb))
* **widget:** name the meter for what it measures — a token budget ([e4705b0](https://github.com/extra-org/extra/commit/e4705b0c286e7114f5b2dae4db5dd241357767a2))


### Reverts

* leave the enterprise example exactly as it was ([be900bf](https://github.com/extra-org/extra/commit/be900bfd2b44e86219ec90d11e8d881731cac0da))


### Documentation

* add human-in-the-loop approval guide ([c1e78d1](https://github.com/extra-org/extra/commit/c1e78d175a648492951724acc7d729669456412e))
* add human-in-the-loop approval guide ([d249b6f](https://github.com/extra-org/extra/commit/d249b6fd11403a2e3880c4d9db02e3b015eeb056))
* add human-in-the-loop design ([d9d321b](https://github.com/extra-org/extra/commit/d9d321bee578e1e614d2a774c13d2ff695d1fee3))
* document agent-level `auto` field for HITL approval bypass ([0de6731](https://github.com/extra-org/extra/commit/0de673155bafbcb5c685ac4e29d6b99f5c534812))
* document agent-level auto field for HITL ([e43851b](https://github.com/extra-org/extra/commit/e43851bedec370b912d1e61cfdf7181083cfc35f))
* **hitl:** document session approval scope and lifecycle ([a7dfedd](https://github.com/extra-org/extra/commit/a7dfedd39e4397c6768d4760e00c491df2accc64))
* restore README docs paths ([a083a24](https://github.com/extra-org/extra/commit/a083a24ee80437c1e7a422fd54cc408e2950e0db))
* **runtime:** document conversational MCP validation ([1e44ffe](https://github.com/extra-org/extra/commit/1e44ffeaa33b34e086567593230da2c622089330))
* update hidm docs ([bf60f9e](https://github.com/extra-org/extra/commit/bf60f9eaf7d9e0fa51ea21fbba63645c83b7b58b))
* update YAML and add auto property ([2e3d05e](https://github.com/extra-org/extra/commit/2e3d05e537c56dfcac5e2c7b90fcc2352467883e))

## [0.3.1](https://github.com/extra-org/extra/compare/agent-engine-v0.3.0...agent-engine-v0.3.1) (2026-07-12)


### Bug Fixes

* Docker entrypoint can't launch agent-manager (documented widget quickstart is broken) ([ae2a8d6](https://github.com/extra-org/extra/commit/ae2a8d605877cbd8ad5389365f78224a41b2ffbc))

## [0.3.0](https://github.com/extra-org/extra/compare/agent-engine-v0.2.2...agent-engine-v0.3.0) (2026-07-12)


### Features

* add support in gemini client ([1592c92](https://github.com/extra-org/extra/commit/1592c92abd5b18368b4a8f95fef3728729be5a60))
* add support in openai client ([66a3f90](https://github.com/extra-org/extra/commit/66a3f906aec8da86c1cb9245e1c097706f06bca0))


### Bug Fixes

* AccessFilter fails open (run crash) when the access resolver raises ([02cfdd1](https://github.com/extra-org/extra/commit/02cfdd15c4d9f85e87901fad27295769ded6fc49))
* fail closed when the access resolver raises ([5846ef4](https://github.com/extra-org/extra/commit/5846ef4ca8a1eb6ca658843b9a482f0763eb4c38))
* navbar.primary.href needs a leading slash ([e5c019b](https://github.com/extra-org/extra/commit/e5c019b533da220540bc6689b5675695c4bd5d2a))
* pin numpy&lt;2.2 in dev extras so mypy passes on fresh installs ([e4ce294](https://github.com/extra-org/extra/commit/e4ce2949b7989805bdb4d751fad280b2c95e07dc))
* pin numpy&lt;2.2 in dev extras so mypy passes on fresh installs ([a139d31](https://github.com/extra-org/extra/commit/a139d31cec2c30b082541ccadc165ad8b97ccf24))
* secret scan rejects benign prose ("password reset") while missing real keys (sk-...) ([8fd8445](https://github.com/extra-org/extra/commit/8fd8445d2e129d42e49f9bcfd9fc471f45cd9483))
* stop secret scan from rejecting prose that mentions secret words ([7c2c3b9](https://github.com/extra-org/extra/commit/7c2c3b96320186eaff56782fc94b06a3b40b3a97))
* use current Mintlify docs.json schema for navbar/footer GitHub link ([93a0c10](https://github.com/extra-org/extra/commit/93a0c106aef6a98895bc92c0367220b6b6f2e397))
* validate agent temperature configuration ([6f5ec15](https://github.com/extra-org/extra/commit/6f5ec15dacbde317a4cc087375833c59263842f2))
* validate agent temperature configuration ([4fd6618](https://github.com/extra-org/extra/commit/4fd66186b6ccbb4b92206d0a6ae895e80f4c6b06))


### Documentation

* add agent prompts to README example ([781d2bc](https://github.com/extra-org/extra/commit/781d2bcebc832399b8e0acf92efb45fd72a0ad1b))
* add mkdir command for prompt subdirectories in tutorial Step 9 ([02df686](https://github.com/extra-org/extra/commit/02df6869aaed922d5dfc24c0117ee105021359b7))
* API reference examples don't match the implemented contract ([9f68c75](https://github.com/extra-org/extra/commit/9f68c756d690805da304a9c4bbf2395e207b481d))
* claude code setup skill ([8921568](https://github.com/extra-org/extra/commit/8921568c32b110bd061ddceaccf443e1350ac34b))
* make the HTTP API reference match the implemented contract ([d3640e1](https://github.com/extra-org/extra/commit/d3640e1c6c3a53696109a520d3e010c11d3556db))
* remove stray character in CLAUDE.md heading ([69c7e6d](https://github.com/extra-org/extra/commit/69c7e6d2251cc224a9b0316fc0dedff2b6f6b38c))
* remove stray character in CLAUDE.md heading ([5da01bb](https://github.com/extra-org/extra/commit/5da01bbb20bf4fc1dfdbf4d05110a9c4697e65d5))
* update repo and image references to extra-org ([d0be7d1](https://github.com/extra-org/extra/commit/d0be7d1f87400569cc8c6f98aa889dcb597d1005))

## [0.2.2](https://github.com/extra-org/extra/compare/agent-engine-v0.2.1...agent-engine-v0.2.2) (2026-07-10)


### Bug Fixes

* remove static version field conflicting with dynamic hatch versioning ([136feaf](https://github.com/extra-org/extra/commit/136feaf5a9fa51a7eb12dd27efa4da1f60d379a5))

## [0.2.1](https://github.com/extra-org/extra/compare/agent-engine-v0.2.0...agent-engine-v0.2.1) (2026-07-10)


### Bug Fixes

* build container in the same run as release-please, not on release event ([ae7181c](https://github.com/extra-org/extra/commit/ae7181cc240ffe0ba3f7444f89a76d7cfd8087a2))

## [0.2.0](https://github.com/extra-org/extra/compare/agent-engine-v0.1.0...agent-engine-v0.2.0) (2026-07-10)


### Features

* add _init__ file to mark it as python package and enable imporing ([ea07e64](https://github.com/extra-org/extra/commit/ea07e644665941630be2779c5a804671c29f32ea))
* add --log-llm flag for on-demand LLM conversation tracing ([c2694db](https://github.com/extra-org/extra/commit/c2694db7e9c526d5c103a9fb40dee3dbb345ce55))
* add "transform_tool_result" hook that enable to add manipulation and logic on the response before reach to the llm ([d49677a](https://github.com/extra-org/extra/commit/d49677a7ff3376d3a5bcffad60b4386735da466d))
* add Amazon Bedrock model provider support ([f67a836](https://github.com/extra-org/extra/commit/f67a836ab02f327ca4889414679be2ad3005529c))
* add chat for demo propose ([de41c29](https://github.com/extra-org/extra/commit/de41c29cbe4f4023d266067c86c62d4c9c464d72))
* add cli.py with validate/generate/run commands ([9384c94](https://github.com/extra-org/extra/commit/9384c941a68b901ff5cbead370f2514115a8ecfa))
* add command to generate agents: ([7f9df43](https://github.com/extra-org/extra/commit/7f9df43ab71985175335d533983f0e8105433043))
* add core/ layer — spec dataclasses, parser ABC, validator ([018cc5f](https://github.com/extra-org/extra/commit/018cc5f2c0dfc651640f8962eb3a4c7d07fc91a1))
* add engine/langgraph/engine.py with RouteFilter ([4962369](https://github.com/extra-org/extra/commit/4962369e283fdf4aa9e3fa51b09f9c93b76bd8d7))
* add engine/types.py + engine/engine.py ABC ([54bd8e1](https://github.com/extra-org/extra/commit/54bd8e159fa1acceafb38ff8d97b239a1481c812))
* add example mcp server ([81160ab](https://github.com/extra-org/extra/commit/81160ab52b94bffceb8a15e86b24504ddf062522))
* add example of usage in langGraph ([9bb264e](https://github.com/extra-org/extra/commit/9bb264e5958eae67f42c7040175c4f227c2e7407))
* add generate/generator.py ([8669583](https://github.com/extra-org/extra/commit/8669583a021418d03ca14e6e202f8e5ff47a9188))
* add loaders/ + migrate resolver plugins to class Resolver convention ([6eb0093](https://github.com/extra-org/extra/commit/6eb00932bd87b3369f3ab0cd585b42263dd7574b))
* add more plugins ([c68e272](https://github.com/extra-org/extra/commit/c68e272ac02e808830e6f6a677189b8416ab0b58))
* add parsers/yaml_parser.py ([fd30d09](https://github.com/extra-org/extra/commit/fd30d091ce3796d7639782717329b0499bec39cc))
* add per-MCP auth plugin system ([858ceef](https://github.com/extra-org/extra/commit/858ceef626962e126b31340bad599b61cf1f06bf))
* add pluggable observability via LangChain callbacks ([bf47e48](https://github.com/extra-org/extra/commit/bf47e48d36035d5015c4eba2a5183165e56b5889))
* add prompts example ([293bbac](https://github.com/extra-org/extra/commit/293bbac15723ea7b8b0cba2e4bf069cb7bfb9447))
* add sessionId to the flow ([32a8361](https://github.com/extra-org/extra/commit/32a8361d48582761c6f574328fe52ffc1e131466))
* add tool usage summary to CLI run output ([850440c](https://github.com/extra-org/extra/commit/850440cd3ab87484d4bd409af551fe5cab93e24f))
* build resolvers flow ([5438a4d](https://github.com/extra-org/extra/commit/5438a4dfe74c266fa675038a4675728fecda87ce))
* build yaml validator ([0c02a9f](https://github.com/extra-org/extra/commit/0c02a9f15c4354cc1881a48cbf16170cc7ab5421))
* change config and YAML file base on the expected schema ([8280fd1](https://github.com/extra-org/extra/commit/8280fd1c390fc355868bd41030d8e9320891295b))
* clean mcp resource when done ([51c8fdf](https://github.com/extra-org/extra/commit/51c8fdf9f5db4811a698a65bd7f45c4da05ec328))
* **cli:** add offline `validate` and `inspect` commands ([7a1fde3](https://github.com/extra-org/extra/commit/7a1fde3a82bba87429db910fd5e3c0a7404fbe4a))
* configure logger ([6f31c59](https://github.com/extra-org/extra/commit/6f31c594652c37bd2d8bd3309531aa2f39692bc4))
* create general mcp client ([2c95572](https://github.com/extra-org/extra/commit/2c9557243ac116b0973c06bed3f04017aad67eab))
* create tool adapter to invoke tool base on the state(remote-mcp, or local tool) ([97bc1f3](https://github.com/extra-org/extra/commit/97bc1f3ced0d5c56ac4ae0c1725f731ab29f83a2))
* create tool registry that each tool in the system (mcp or local tool) register and abstract the work with langGraph ([24d6cc8](https://github.com/extra-org/extra/commit/24d6cc8a823f63eb21c43bb20bef6a61f29265d2))
* **demo:** real widget-&gt;agent-&gt;sub-agent flow demo page ([d30ef54](https://github.com/extra-org/extra/commit/d30ef5446b755e3d54ac8e3bb90d1661b919a7ae))
* dev runtime hooks ([2d8dab3](https://github.com/extra-org/extra/commit/2d8dab38e385b4f4a7a0e8f2e643a2ce6a24366d))
* **engine:** real token tracking and conversation budget enforcement ([0e8e84e](https://github.com/extra-org/extra/commit/0e8e84e185165c27f996bdaa0250e8ae519cd899))
* **examples:** deterministic widget sub-agent demo config ([4ba5100](https://github.com/extra-org/extra/commit/4ba5100ba1c936da6c37271d2b1716a4db6ac82c))
* extend the database-backed conversation persistence layer ([a5fd4e6](https://github.com/extra-org/extra/commit/a5fd4e66251b1e9711f2d17e885f26e4b7c6f4de))
* first phase of engine ([cf19eb8](https://github.com/extra-org/extra/commit/cf19eb82976b38f5a348ab726d4863145b8ec000))
* generate examples files that need to be implemented by the client ([7bd43ba](https://github.com/extra-org/extra/commit/7bd43ba3ca1675cbdc41a90487d887e5f93e0f8a))
* invoke hooks base on stage ([c50810d](https://github.com/extra-org/extra/commit/c50810d04c7808aafc8a16039830303909a283fd))
* modularize agent chat widget ([3883dfe](https://github.com/extra-org/extra/commit/3883dfe0eab4430e6893fa39ded88acdf9996b02))
* replace MCPManager with langchain-mcp-adapters ([ab80719](https://github.com/extra-org/extra/commit/ab80719eb85e5389ef2b970569b0467403562446))
* restore shared vs agent resolver scope with inheritance ([c4f1550](https://github.com/extra-org/extra/commit/c4f15502452992a31697bd6f3c8bb387b08b0d50))
* runtime-enforced execution limits ([c710fcc](https://github.com/extra-org/extra/commit/c710fcccef91eda39cf7d422f443f7ac9da2a0c9))
* serve agent systems over HTTP via `agentctl serve` ([4deb2ed](https://github.com/extra-org/extra/commit/4deb2ed5d52703a846754a0f800810a5aebb3971))
* streaming support ([e697430](https://github.com/extra-org/extra/commit/e6974304f528103a819c74dfd3ea142fa06f7455))
* structured logging and always-on observability ([226f8ac](https://github.com/extra-org/extra/commit/226f8acff6e329b468b2c9b097cc92414cd2c1af))
* support tags in agent YAML and also support tags in transport (if provided) ([5ca80bb](https://github.com/extra-org/extra/commit/5ca80bb32a5acbf5710d20fe7f7465d872e41f7e))
* update runtime to generate resolver classes base on the agents names ([79ad5f9](https://github.com/extra-org/extra/commit/79ad5f994dd76f5961d59057d6c301f4b01e82f5))
* validate hooks ([42de791](https://github.com/extra-org/extra/commit/42de791d717d70ab3dd75400971ba27ceb3e6405))
* **widget:** add browser demos, e2e tests, and accessibility polish ([5cd7e29](https://github.com/extra-org/extra/commit/5cd7e298baf1bc201ba20470a55a7476ec9d8aef))
* **widget:** emit safe routing metadata as agent-chat:answer ([f8218a4](https://github.com/extra-org/extra/commit/f8218a4d08308763615a46ea9d7608ca4b54242e))
* **widget:** render chat with React ([3a26a4e](https://github.com/extra-org/extra/commit/3a26a4e9143f3f47a7fef494b9b44e1936c42d0b))
* **widget:** stream agent responses ([4531602](https://github.com/extra-org/extra/commit/4531602a8060c86b78f094780ab64991ee3a5b40))
* **widget:** use shadcn AI chat primitives ([c38e3ea](https://github.com/extra-org/extra/commit/c38e3eadbbddb17d8491bdcf04990e419e9abb00))


### Bug Fixes

* add generic context to GraphState ([6fc979d](https://github.com/extra-org/extra/commit/6fc979d1f1271338d59983f8f2c51bd0a4349a64))
* **local-mcp:** disable uvicorn websockets to silence deprecation warnings ([aa907b2](https://github.com/extra-org/extra/commit/aa907b2f3dec4b6c70f7210ab2507b450ba6f03d))
* make the config JSON Schema's $id resolvable ([4f094ad](https://github.com/extra-org/extra/commit/4f094adb77baa7b28f61af4e5727d306fdc9916f))
* preserve application logging during database migrations ([9b1b9da](https://github.com/extra-org/extra/commit/9b1b9dadc6aa442eb814c6412f023954be87fb57))
* remove not used tables ([fcc0f39](https://github.com/extra-org/extra/commit/fcc0f39200a9f4e17333a39a900bf6be90cc9f53))
* resolve MCP auth headers per request, not once at startup ([b35b181](https://github.com/extra-org/extra/commit/b35b181528ff7370a28ca75b18b3357750ba500c))
* tool registry hold providers, he is not provider ([fa3c11b](https://github.com/extra-org/extra/commit/fa3c11b4b6bab70981f97a70cc746552fceda06c))
* **widget:** recover stale agent conversations ([64523b9](https://github.com/extra-org/extra/commit/64523b94aeec8db49d65bf733a81adbad3296daf))


### Documentation

* add foundational documentation for the Declarative Agent Platform, including AGENTS.md, CLAUDE.md, and various design documents in the docs directory ([a4a8cbe](https://github.com/extra-org/extra/commit/a4a8cbe8d1620a0c21fa30e8fc38da054c5e2a04))
* add Mintlify documentation site ([7cb81b2](https://github.com/extra-org/extra/commit/7cb81b2561658ec74b591481dcafb240da68adcc))
* add observability page — structured logging and Langfuse tracing ([46b3436](https://github.com/extra-org/extra/commit/46b34369532e3241db0a58b65f2a96ea02cedfca))
* add runtime hooks ([073f264](https://github.com/extra-org/extra/commit/073f264afed137c5986a7a9d3060259813b639ba))
* add theme field to docs.json ([c6a46b8](https://github.com/extra-org/extra/commit/c6a46b8c75db07226b04ab38300007fabe585d33))
* align with supervisor pattern and current engine/MCP reality ([de0f5af](https://github.com/extra-org/extra/commit/de0f5afb80a889fd09a4a8e5fbe9c11b71b7317c))
* alignment with the actual logic ([42a4bb8](https://github.com/extra-org/extra/commit/42a4bb8eabf0e2c6bc92b07de22ef1f6c6c3d2e6))
* clarify execution phases and input policies in ARCHITECTURE.md, MCP_AND_TOOLS.md, and YAML_SPEC.md; enhance AGENTS.md with detailed agent lifecycle and context resolution explanations ([8f97b6f](https://github.com/extra-org/extra/commit/8f97b6f1e38b0984dd7792caa8b8f1e3f68e53b3))
* document the real agent/sub-agent flow demo ([d94c846](https://github.com/extra-org/extra/commit/d94c84622228d68291ccd060ceeb95fe78df3e14))
* enhance AGENTS.md with Claude Code integration details and update .gitignore for local configuration files ([f99c711](https://github.com/extra-org/extra/commit/f99c711cb0700d6f48b66e5f608594d34906c7d6))
* enhance documentation on reusable agent definitions and instances in ARCHITECTURE.md, YAML_SPEC.md, and related files, including validation rules and execution details ([78447ee](https://github.com/extra-org/extra/commit/78447ee171110772900cea2622609ccb207463c2))
* **examples:** add environment template for flagship example ([c6ae485](https://github.com/extra-org/extra/commit/c6ae485fd414e1be3fcd9cf3df60e132e81ac85a))
* **examples:** promote enterprise knowledge assistant example ([53f73a4](https://github.com/extra-org/extra/commit/53f73a4e83de924400a609849da5a12dfd4999b0))
* **examples:** replace local demo branding with global terminology ([40510c9](https://github.com/extra-org/extra/commit/40510c976add03ed5245f9237e1c3bb168811044))
* expand ARCHITECTURE.md to provide a comprehensive overview of the product's design, emphasizing the role of YAML as an input specification, detailing the build and runtime phases, and outlining the developer experience with the `agentctl` CLI. ([6a16353](https://github.com/extra-org/extra/commit/6a16353f18c9b3da7a017ee4b5ea616beab72a86))
* extend the database-backed conversation persistence layer ([b8d5aef](https://github.com/extra-org/extra/commit/b8d5aef746af05bfc4c18009c31beca4f58cdd5f))
* README, MCP_AND_TOOLS, RUNTIME_HOOKS, local MCP server README. ([7a1fde3](https://github.com/extra-org/extra/commit/7a1fde3a82bba87429db910fd5e3c0a7404fbe4a))
* **readme:** clarify runtime modes and flagship example usage ([513c1a5](https://github.com/extra-org/extra/commit/513c1a5445c965908ecf3c62cb39ca58a0c9ac13))
* refine AGENTS.md with additional examples and clarify usage scenarios; update related documentation for consistency and clarity ([dcd9218](https://github.com/extra-org/extra/commit/dcd92181653a9bd089600e9b1319835ab362043f))
* refresh documentation for public release ([f1a9e3f](https://github.com/extra-org/extra/commit/f1a9e3fd6bec20372f72e0d5be8b9e5fb2294386))
* rename mint.json to docs.json for Mintlify v2 ([a85b25c](https://github.com/extra-org/extra/commit/a85b25c3524ec0c63d18a74ae903431c27961cc1))
* restructure AGENTS.md and CLAUDE.md to centralize AI instruction system under .ai/; remove redundant skills and roles from .claude/; update Makefile and README.md for consistency with new structure ([c607638](https://github.com/extra-org/extra/commit/c607638ed4b077e861d85a78aa4108971646bf23))
* revise ARCHITECTURE.md to clarify the platform's role as an open-source, self-hosted runtime for AI-agent systems, emphasizing YAML as the declarative specification layer and detailing its integration into the runtime model. ([37ca799](https://github.com/extra-org/extra/commit/37ca7998d5b9df460657a82392341d162436ac6b))
* rewrite README around product vision + set MIT license ([bbf5f06](https://github.com/extra-org/extra/commit/bbf5f06e715643c112e0b74a5a5186603b982a49))
* support tags in agent YAML ([21cfb52](https://github.com/extra-org/extra/commit/21cfb52aa70c6c705dcd3d420cfb801e0a3bb185))
* update AGENTS.md and Makefile for improved developer setup instructions; enhance pyproject.toml with runtime dependencies and CLI entrypoints ([94bebc5](https://github.com/extra-org/extra/commit/94bebc55d75ca4deb35b88caf6a93aaff33125d2))
* update AGENTS.md with prompt rendering guidelines and enhance ARCHITECTURE.md and PROMPT_RENDERING.md with detailed context resolution and sidecar model explanations ([fd63168](https://github.com/extra-org/extra/commit/fd631684b1faf4403b725ebe75de5c58724b8d7e))
* update docs base on the new format - shared resolvers, difference subclasses for implementations ([9019b8c](https://github.com/extra-org/extra/commit/9019b8c0bc8357b866e310c40d645e339c315533))
* update docs base on the new implementation and changes ([06e6c8f](https://github.com/extra-org/extra/commit/06e6c8f43b702bea392f1f92f3b5f125db0bfe78))
* update docs base on yaml validator ([278e689](https://github.com/extra-org/extra/commit/278e689dee13c60bbe0ee36c81c9ed6d5437bde8))
* update docs with hooks ([b1205b2](https://github.com/extra-org/extra/commit/b1205b220d2a5578169b980c4a359d75283fada2))
* update readme base on the current status ([c068d66](https://github.com/extra-org/extra/commit/c068d6698f3b15398468582dc1bfabdf16fc3db3))
* update README.md ([1aa4336](https://github.com/extra-org/extra/commit/1aa4336603563d456d4713be12e05c2bbf629c1b))
