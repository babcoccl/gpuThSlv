# Licenses

Private-use installation: this system is run for the operator's own use and is
**never redistributed** (no images, binaries, or packages are published or
shared). The notes below are an inventory, not legal advice.

## Headline components

| component | license | role |
|---|---|---|
| GPU-CFR (engine) | **none found upstream** — no license file in [lbn187/GPU-CFR](https://github.com/lbn187/GPU-CFR); its README carries a CC BY-NC-SA 4.0 badge only | runtime (engine workers) |
| TexasSolver | AGPL v3 (upstream repo `bupticybee/TexasSolver`; author also forbids redistributing the binary) | validation only — Phase 7 cross-check scripts; never installed by the release |
| NVIDIA driver + CUDA toolkit | NVIDIA proprietary (driver + `cuda-toolkit` pip package reports UNKNOWN) | runtime |
| This repository | no license file (private) | — |

GPU-CFR plainly: upstream ships compiled `.so` modules with **no license
file**; a CC BY-NC-SA badge in its README suggests non-commercial,
share-alike terms. This system is private-use and is not redistributed; the
install expects the user's own GPU-CFR copy at the configured path
(`third_party/GPU-CFR`, a read-only submodule clone of upstream). Nothing in
this repository modifies or relicenses it.

## Python — API venv (`.venv-api`, runtime)

Generated with `pip-licenses --format=markdown` on 2026-11-18.

| Name                   | Version      | License                                                                                             |
|------------------------|--------------|-----------------------------------------------------------------------------------------------------|
| Jinja2                 | 3.1.6        | BSD License                                                                                         |
| MarkupSafe             | 2.1.5        | BSD License                                                                                         |
| Pygments               | 2.21.0       | BSD-2-Clause                                                                                        |
| annotated-doc          | 0.0.6        | MIT                                                                                                 |
| annotated-types        | 0.8.0        | MIT                                                                                                 |
| anyio                  | 4.16.0       | MIT                                                                                                 |
| certifi                | 2026.10.23   | Mozilla Public License 2.0 (MPL 2.0)                                                                |
| click                  | 8.5.1        | BSD-3-Clause                                                                                        |
| contourpy              | 1.4.0        | BSD-3-Clause                                                                                        |
| cuda-toolkit           | 13.0.3.0     | UNKNOWN                                                                                             |
| cycler                 | 0.12.1       | BSD License                                                                                         |
| fastapi                | 0.163.1      | MIT                                                                                                 |
| filelock               | 4.2.4        | MIT                                                                                                 |
| fonttools              | 4.68.0       | MIT                                                                                                 |
| fsspec                 | 2026.9.0     | BSD-3-Clause                                                                                        |
| h11                    | 0.16.0       | MIT License                                                                                         |
| httpcore               | 1.0.9        | BSD-3-Clause                                                                                        |
| httpx                  | 0.28.1       | BSD License                                                                                         |
| idna                   | 3.20         | BSD-3-Clause                                                                                        |
| iniconfig              | 2.3.1        | MIT                                                                                                 |
| kiwisolver             | 1.5.1        | BSD License                                                                                         |
| matplotlib             | 3.11.2       | Python Software Foundation License                                                                  |
| mpmath                 | 1.3.0        | BSD License                                                                                         |
| networkx               | 3.7          | BSD-3-Clause                                                                                        |
| numpy                  | 2.5.5        | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0                                                  |
| nvidia-cublas          | 13.1.1.3     | LicenseRef-NVIDIA-Proprietary                                                                       |
| nvidia-cuda-cupti      | 13.0.85      | Other/Proprietary License                                                                           |
| nvidia-cuda-nvrtc      | 13.0.88      | Other/Proprietary License                                                                           |
| nvidia-cuda-runtime    | 13.0.96      | LicenseRef-NVIDIA-Proprietary                                                                       |
| nvidia-cudnn-cu13      | 9.24.0.43    | LicenseRef-NVIDIA-Proprietary                                                                       |
| nvidia-cufft           | 12.0.0.61    | Other/Proprietary License                                                                           |
| nvidia-cufile          | 1.15.1.6     | Other/Proprietary License                                                                           |
| nvidia-curand          | 10.4.0.35    | Other/Proprietary License                                                                           |
| nvidia-cusolver        | 12.0.4.66    | Other/Proprietary License                                                                           |
| nvidia-cusparse        | 12.6.3.3     | Other/Proprietary License                                                                           |
| nvidia-cusparselt-cu13 | 0.8.1        | NVIDIA Proprietary Software                                                                         |
| nvidia-ml-py           | 13.615.71    | BSD License                                                                                         |
| nvidia-nccl-cu13       | 2.30.7       | LicenseRef-NVIDIA-Proprietary                                                                       |
| nvidia-nvjitlink       | 13.4.92      | LicenseRef-NVIDIA-Proprietary                                                                       |
| nvidia-nvshmem-cu13    | 3.4.5        | LicenseRef-NVIDIA-Proprietary                                                                       |
| nvidia-nvtx            | 13.0.85      | Other/Proprietary License                                                                           |
| opentelemetry-api      | 1.45.2       | Apache-2.0                                                                                          |
| packaging              | 26.3         | Apache-2.0 OR BSD-2-Clause                                                                          |
| pillow                 | 13.0.0       | MIT-CMU                                                                                             |
| pip                    | 26.3         | MIT                                                                                                 |
| pip-licenses           | 5.5.5        | MIT                                                                                                 |
| pluggy                 | 1.7.0        | MIT License                                                                                         |
| prettytable            | 3.19.0       | BSD-3-Clause                                                                                        |
| prometheus_client      | 0.26.0       | Apache-2.0 AND BSD-2-Clause                                                                         |
| pydantic               | 2.14.3       | MIT                                                                                                 |
| pydantic_core          | 2.50.4       | MIT                                                                                                 |
| pyparsing              | 3.3.3        | MIT                                                                                                 |
| pytest                 | 9.2.0        | MIT                                                                                                 |
| python-dateutil        | 4.0.0        | BSD-3-Clause                                                                                        |
| setuptools             | 78.1.0       | MIT License                                                                                         |
| starlette              | 1.10.0       | BSD-3-Clause                                                                                        |
| sympy                  | 1.14.0       | BSD License                                                                                         |
| torch                  | 2.14.1+cu130 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |
| typing-inspection      | 0.4.5        | MIT                                                                                                 |
| typing_extensions      | 4.16.0       | PSF-2.0                                                                                             |
| tzdata                 | 2026.6       | Apache-2.0                                                                                          |
| uvicorn                | 0.54.0       | BSD-3-Clause                                                                                        |
| wcwidth                | 0.9.4        | MIT License                                                                                         |

## Python — engine venv (`.venv-engine`, runtime)

| Name                     | Version   | License                     |
|--------------------------|-----------|-----------------------------|
| Jinja2                   | 3.1.6     | BSD License                 |
| MarkupSafe               | 3.0.4     | BSD-3-Clause                |
| Pygments                 | 2.21.0    | BSD-2-Clause                |
| exceptiongroup           | 1.3.1     | MIT License                 |
| filelock                 | 3.19.1    | Unlicense                   |
| fsspec                   | 2025.10.0 | BSD-3-Clause                |
| importlib_metadata       | 8.7.1     | Apache-2.0                  |
| iniconfig                | 2.1.0     | MIT                         |
| mpmath                   | 1.3.0     | BSD License                 |
| networkx                 | 3.2.1     | BSD License                 |
| numpy                    | 2.0.2     | BSD License                 |
| nvidia-cublas-cu12       | 12.8.4.1  | Other/Proprietary License   |
| nvidia-cuda-cupti-cu12   | 12.8.90   | Other/Proprietary License   |
| nvidia-cuda-nvrtc-cu12   | 12.8.93   | Other/Proprietary License   |
| nvidia-cuda-runtime-cu12 | 12.8.90   | Other/Proprietary License   |
| nvidia-cudnn-cu12        | 9.10.2.21 | Other/Proprietary License   |
| nvidia-cufft-cu12        | 11.3.3.83 | Other/Proprietary License   |
| nvidia-cufile-cu12       | 1.13.1.3  | Other/Proprietary License   |
| nvidia-curand-cu12       | 10.3.9.90 | Other/Proprietary License   |
| nvidia-cusolver-cu12     | 11.7.3.90 | Other/Proprietary License   |
| nvidia-cusparse-cu12     | 12.5.8.93 | Other/Proprietary License   |
| nvidia-cusparselt-cu12   | 0.7.1     | NVIDIA Proprietary Software |
| nvidia-nccl-cu12         | 2.27.3    | Other/Proprietary License   |
| nvidia-nvjitlink-cu12    | 12.8.93   | Other/Proprietary License   |
| nvidia-nvtx-cu12         | 12.8.90   | Other/Proprietary License   |
| packaging                | 26.3      | Apache-2.0 OR BSD-2-Clause  |
| pluggy                   | 1.6.0     | MIT License                 |
| pytest                   | 8.4.2     | MIT License                 |
| sympy                    | 1.14.0    | BSD License                 |
| torch                    | 2.8.0     | BSD License                 |
| triton                   | 3.4.0     | MIT License                 |
| typing_extensions        | 4.16.0    | PSF-2.0                     |
| zipp                     | 3.23.1    | MIT                         |

## npm — `ui/` (runtime + dev/build)

Generated with `license-checker --markdown`. `holdem-solver-ui` itself is
UNLICENSED (private). `devDependencies` are build/dev only; `dependencies`
(`react`, `react-dom`, `react-router-dom`, `@tanstack/react-query`,
`openapi-fetch`, all MIT) ship in `ui/dist`.

[@axe-core/playwright@4.14.0](https://github.com/dequelabs/axe-core-npm) - MPL-2.0
[@babel/code-frame@7.29.7](https://github.com/babel/babel) - MIT
[@babel/compat-data@7.29.7](https://github.com/babel/babel) - MIT
[@babel/core@7.29.13](https://github.com/babel/babel) - MIT
[@babel/generator@7.29.14](https://github.com/babel/babel) - MIT
[@babel/helper-compilation-targets@7.29.13](https://github.com/babel/babel) - MIT
[@babel/helper-globals@7.29.7](https://github.com/babel/babel) - MIT
[@babel/helper-module-imports@7.29.7](https://github.com/babel/babel) - MIT
[@babel/helper-module-transforms@7.29.14](https://github.com/babel/babel) - MIT
[@babel/helper-plugin-utils@7.29.7](https://github.com/babel/babel) - MIT
[@babel/helper-string-parser@7.29.14](https://github.com/babel/babel) - MIT
[@babel/helper-validator-identifier@7.29.7](https://github.com/babel/babel) - MIT
[@babel/helper-validator-option@7.29.7](https://github.com/babel/babel) - MIT
[@babel/helpers@7.29.14](https://github.com/babel/babel) - MIT
[@babel/parser@7.29.14](https://github.com/babel/babel) - MIT
[@babel/plugin-transform-react-jsx-self@7.29.7](https://github.com/babel/babel) - MIT
[@babel/plugin-transform-react-jsx-source@7.29.7](https://github.com/babel/babel) - MIT
[@babel/template@7.29.7](https://github.com/babel/babel) - MIT
[@babel/traverse@7.29.14](https://github.com/babel/babel) - MIT
[@babel/types@7.29.14](https://github.com/babel/babel) - MIT
[@cacheable/memory@2.2.0](https://github.com/jaredwray/cacheable) - MIT
[@cacheable/utils@2.5.0](https://github.com/jaredwray/cacheable) - MIT
[@esbuild/linux-x64@0.28.2](https://github.com/evanw/esbuild) - MIT
[@eslint-community/eslint-utils@4.10.1](https://github.com/eslint-community/eslint-utils) - MIT
[@eslint-community/regexpp@4.12.3](https://github.com/eslint-community/regexpp) - MIT
[@eslint/config-array@0.23.6](https://github.com/eslint/rewrite) - Apache-2.0
[@eslint/config-helpers@0.7.0](https://github.com/eslint/rewrite) - Apache-2.0
[@eslint/core@1.2.1](https://github.com/eslint/rewrite) - Apache-2.0
[@eslint/object-schema@3.0.5](https://github.com/eslint/rewrite) - Apache-2.0
[@eslint/plugin-kit@0.7.3](https://github.com/eslint/rewrite) - Apache-2.0
[@humanfs/core@0.19.2](https://github.com/humanwhocodes/humanfs) - Apache-2.0
[@humanfs/node@0.16.8](https://github.com/humanwhocodes/humanfs) - Apache-2.0
[@humanfs/types@0.15.0](https://github.com/humanwhocodes/humanfs) - Apache-2.0
[@humanwhocodes/module-importer@1.0.1](https://github.com/humanwhocodes/module-importer) - Apache-2.0
[@humanwhocodes/retry@0.4.3](https://github.com/humanwhocodes/retry) - Apache-2.0
[@isaacs/nonrecursive-glob-match@1.1.0](undefined) - BlueOak-1.0.0
[@jridgewell/gen-mapping@0.3.13](https://github.com/jridgewell/sourcemaps) - MIT
[@jridgewell/remapping@2.3.5](https://github.com/jridgewell/sourcemaps) - MIT
[@jridgewell/resolve-uri@3.1.3](https://github.com/jridgewell/resolve-uri) - MIT
[@jridgewell/sourcemap-codec@1.6.0](https://github.com/jridgewell/sourcemaps) - MIT
[@jridgewell/trace-mapping@0.3.31](https://github.com/jridgewell/sourcemaps) - MIT
[@keyv/bigmap@1.3.1](https://github.com/jaredwray/keyv) - MIT
[@keyv/serialize@1.1.1](https://github.com/jaredwray/keyv) - MIT
[@playwright/test@1.65.1](https://github.com/microsoft/playwright) - Apache-2.0
[@redocly/ajv@8.11.2](https://github.com/ajv-validator/ajv) - MIT
[@redocly/config@0.22.0](undefined) - MIT
[@redocly/openapi-core@1.34.24](https://github.com/Redocly/redocly-cli) - MIT
[@rolldown/pluginutils@1.0.0-rc.3](https://github.com/rolldown/rolldown) - MIT
[@rollup/rollup-linux-x64-gnu@4.68.5](https://github.com/rollup/rollup) - MIT
[@rollup/rollup-linux-x64-musl@4.68.5](https://github.com/rollup/rollup) - MIT
[@tanstack/query-core@5.108.0](https://github.com/TanStack/query) - MIT
[@tanstack/react-query@5.108.0](https://github.com/TanStack/query) - MIT
[@types/babel__core@7.20.5](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@types/babel__generator@7.27.0](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@types/babel__template@7.4.4](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@types/babel__traverse@7.28.0](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@types/esrecurse@4.3.1](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@types/estree@1.0.10](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@types/json-schema@7.0.15](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@types/node@26.6.6](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@types/react-dom@19.3.0](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@types/react@19.3.0](https://github.com/DefinitelyTyped/DefinitelyTyped) - MIT
[@typescript-eslint/eslint-plugin@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@typescript-eslint/parser@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@typescript-eslint/project-service@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@typescript-eslint/scope-manager@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@typescript-eslint/tsconfig-utils@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@typescript-eslint/type-utils@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@typescript-eslint/types@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@typescript-eslint/typescript-estree@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@typescript-eslint/utils@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@typescript-eslint/visitor-keys@8.73.0](https://github.com/typescript-eslint/typescript-eslint) - MIT
[@vitejs/plugin-react@5.2.0](https://github.com/vitejs/vite-plugin-react) - MIT
[acorn-jsx@5.3.2](https://github.com/acornjs/acorn-jsx) - MIT
[acorn@8.19.0](https://github.com/acornjs/acorn) - MIT
[agent-base@7.1.4](https://github.com/TooTallNate/proxy-agents) - MIT
[ajv@6.15.0](https://github.com/ajv-validator/ajv) - MIT
[ajv@8.20.0](https://github.com/ajv-validator/ajv) - MIT
[ansi-colors@4.1.3](https://github.com/doowb/ansi-colors) - MIT
[argparse@2.0.1](https://github.com/nodeca/argparse) - Python-2.0
[axe-core@4.14.1](https://github.com/dequelabs/axe-core) - MPL-2.0
[balanced-match@1.0.2](https://github.com/juliangruber/balanced-match) - MIT
[balanced-match@4.0.4](https://github.com/juliangruber/balanced-match) - MIT
[baseline-browser-mapping@2.11.38](https://github.com/web-platform-dx/baseline-browser-mapping) - Apache-2.0
[brace-expansion@2.1.8](https://github.com/juliangruber/brace-expansion) - MIT
[brace-expansion@5.0.13](https://github.com/juliangruber/brace-expansion) - MIT
[browserslist@4.29.6](https://github.com/browserslist/browserslist) - MIT
[cacheable@2.5.0](https://github.com/jaredwray/cacheable) - MIT
[caniuse-lite@1.0.30001826](https://github.com/browserslist/caniuse-lite) - CC-BY-4.0
[change-case@5.4.4](https://github.com/blakeembrey/change-case) - MIT
[colorette@1.4.0](https://github.com/jorgebucaran/colorette) - MIT
[convert-source-map@2.0.0](https://github.com/thlorenz/convert-source-map) - MIT
[cookie@1.1.1](https://github.com/jshttp/cookie) - MIT
[cross-spawn@7.0.6](https://github.com/moxystudio/node-cross-spawn) - MIT
[csstype@3.2.4](https://github.com/frenic/csstype) - MIT
[debug@4.4.3](https://github.com/debug-js/debug) - MIT
[deep-is@0.1.4](https://github.com/thlorenz/deep-is) - MIT
[electron-to-chromium@1.5.488](https://github.com/Kilian/electron-to-chromium) - ISC
[esbuild@0.28.2](https://github.com/evanw/esbuild) - MIT
[escalade@3.2.0](https://github.com/lukeed/escalade) - MIT
[escape-string-regexp@4.0.0](https://github.com/sindresorhus/escape-string-regexp) - MIT
[eslint-scope@9.1.2](https://github.com/eslint/js) - BSD-2-Clause
[eslint-visitor-keys@3.4.3](https://github.com/eslint/eslint-visitor-keys) - Apache-2.0
[eslint-visitor-keys@5.0.1](https://github.com/eslint/js) - Apache-2.0
[eslint@10.15.0](https://github.com/eslint/eslint) - MIT
[espree@11.2.0](https://github.com/eslint/js) - BSD-2-Clause
[esquery@1.7.0](https://github.com/estools/esquery) - BSD-3-Clause
[esrecurse@4.3.0](https://github.com/estools/esrecurse) - BSD-2-Clause
[estraverse@5.3.0](https://github.com/estools/estraverse) - BSD-2-Clause
[esutils@2.0.3](https://github.com/estools/esutils) - BSD-2-Clause
[fast-deep-equal@3.1.3](https://github.com/epoberezkin/fast-deep-equal) - MIT
[fast-json-stable-stringify@2.1.0](https://github.com/epoberezkin/fast-json-stable-stringify) - MIT
[fast-levenshtein@2.0.6](https://github.com/hiddentao/fast-levenshtein) - MIT
[fast-uri@3.1.9](https://github.com/fastify/fast-uri) - BSD-3-Clause
[fdir@6.5.0](https://github.com/thecodrr/fdir) - MIT
[file-entry-cache@11.1.5](https://github.com/jaredwray/cacheable) - MIT
[find-up@5.0.0](https://github.com/sindresorhus/find-up) - MIT
[flat-cache@6.1.23](https://github.com/jaredwray/cacheable) - MIT
[flatted@3.4.4](https://github.com/WebReflection/flatted) - ISC
[gensync@1.0.0-beta.2](https://github.com/loganfsmyth/gensync) - MIT
[glob-parent@6.0.2](https://github.com/gulpjs/glob-parent) - ISC
[hashery@1.5.1](https://github.com/jaredwray/hashery) - MIT
[holdem-solver-ui@0.1.0](undefined) - UNLICENSED
[hookified@1.15.1](https://github.com/jaredwray/hookified) - MIT
[hookified@2.2.0](https://github.com/jaredwray/hookified) - MIT
[https-proxy-agent@7.0.6](https://github.com/TooTallNate/proxy-agents) - MIT
[ignore@5.3.2](https://github.com/kaelzhang/node-ignore) - MIT
[ignore@7.0.16](https://github.com/kaelzhang/node-ignore) - MIT
[imurmurhash@0.1.4](https://github.com/jensyt/imurmurhash-js) - MIT
[index-to-position@1.2.0](https://github.com/sindresorhus/index-to-position) - MIT
[is-extglob@2.1.1](https://github.com/jonschlinkert/is-extglob) - MIT
[is-glob@4.0.3](https://github.com/micromatch/is-glob) - MIT
[isexe@2.0.0](https://github.com/isaacs/isexe) - ISC
[js-levenshtein@1.1.6](https://github.com/gustf/js-levenshtein) - MIT
[js-tokens@4.0.0](https://github.com/lydell/js-tokens) - MIT
[js-yaml@4.3.2](https://github.com/nodeca/js-yaml) - MIT
[jsesc@3.1.0](https://github.com/mathiasbynens/jsesc) - MIT
[json-schema-traverse@0.4.1](https://github.com/epoberezkin/json-schema-traverse) - MIT
[json-schema-traverse@1.0.0](https://github.com/epoberezkin/json-schema-traverse) - MIT
[json-stable-stringify-without-jsonify@1.0.1](https://github.com/samn/json-stable-stringify) - MIT
[json5@2.2.3](https://github.com/json5/json5) - MIT
[keyv@5.6.0](https://github.com/jaredwray/keyv) - MIT
[levn@0.4.1](https://github.com/gkz/levn) - MIT
[locate-path@6.0.0](https://github.com/sindresorhus/locate-path) - MIT
[lru-cache@5.1.1](https://github.com/isaacs/node-lru-cache) - ISC
[minimatch@11.0.9](https://github.com/isaacs/minimatch) - BlueOak-1.0.0
[minimatch@5.1.9](https://github.com/isaacs/minimatch) - ISC
[ms@2.1.3](https://github.com/vercel/ms) - MIT
[nanoid@3.3.20](https://github.com/ai/nanoid) - MIT
[natural-compare@1.4.0](https://github.com/litejs/natural-compare-lite) - MIT
[node-releases@2.0.65](https://github.com/chicoxyzzy/node-releases) - MIT
[openapi-fetch@0.15.2](https://github.com/openapi-ts/openapi-typescript) - MIT
[openapi-typescript-helpers@0.0.15](https://github.com/openapi-ts/openapi-typescript) - MIT
[openapi-typescript@7.13.0](https://github.com/openapi-ts/openapi-typescript) - MIT
[optionator@0.9.4](https://github.com/gkz/optionator) - MIT
[p-limit@3.1.0](https://github.com/sindresorhus/p-limit) - MIT
[p-locate@5.0.0](https://github.com/sindresorhus/p-locate) - MIT
[parse-json@8.3.0](https://github.com/sindresorhus/parse-json) - MIT
[path-exists@4.0.0](https://github.com/sindresorhus/path-exists) - MIT
[path-key@3.1.1](https://github.com/sindresorhus/path-key) - MIT
[picocolors@1.1.1](https://github.com/alexeyraspopov/picocolors) - ISC
[picomatch@4.0.7](https://github.com/micromatch/picomatch) - MIT
[playwright-core@1.65.1](https://github.com/microsoft/playwright) - Apache-2.0
[playwright@1.65.1](https://github.com/microsoft/playwright) - Apache-2.0
[pluralize@8.0.0](https://github.com/blakeembrey/pluralize) - MIT
[postcss@8.5.33](https://github.com/postcss/postcss) - MIT
[prelude-ls@1.2.1](https://github.com/gkz/prelude-ls) - MIT
[punycode@2.3.1](https://github.com/mathiasbynens/punycode.js) - MIT
[qified@0.10.1](https://github.com/jaredwray/qified) - MIT
[react-dom@19.3.1](https://github.com/react/react) - MIT
[react-refresh@0.18.0](https://github.com/facebook/react) - MIT
[react-router-dom@7.18.5](https://github.com/remix-run/react-router) - MIT
[react-router@7.18.5](https://github.com/remix-run/react-router) - MIT
[react@19.3.1](https://github.com/react/react) - MIT
[require-from-string@2.0.2](https://github.com/floatdrop/require-from-string) - MIT
[rollup@4.68.5](https://github.com/rollup/rollup) - MIT
[scheduler@0.28.0](https://github.com/react/react) - MIT
[semver@6.3.2](https://github.com/npm/node-semver) - ISC
[semver@7.8.6](https://github.com/npm/node-semver) - ISC
[set-cookie-parser@2.7.3](https://github.com/nfriedly/set-cookie-parser) - MIT
[shebang-command@2.0.0](https://github.com/kevva/shebang-command) - MIT
[shebang-regex@3.0.0](https://github.com/sindresorhus/shebang-regex) - MIT
[source-map-js@1.2.2](https://github.com/7rulnik/source-map-js) - BSD-3-Clause
[supports-color@10.2.2](https://github.com/chalk/supports-color) - MIT
[tinyglobby@0.2.17](https://github.com/SuperchupuDev/tinyglobby) - MIT
[ts-api-utils@2.5.0](https://github.com/JoshuaKGoldberg/ts-api-utils) - MIT
[type-check@0.4.0](https://github.com/gkz/type-check) - MIT
[type-fest@4.41.0](https://github.com/sindresorhus/type-fest) - (MIT OR CC0-1.0)
[typescript@5.9.3](https://github.com/microsoft/TypeScript) - Apache-2.0
[undici-types@8.9.0](https://github.com/nodejs/undici) - MIT
[update-browserslist-db@1.4.0](https://github.com/browserslist/update-db) - MIT
[uri-js-replace@1.0.3](https://github.com/andreinwald/uri-js-replace) - MIT
[uri-js@4.4.1](https://github.com/garycourt/uri-js) - BSD-2-Clause
[vite@7.3.8](https://github.com/vitejs/vite) - MIT
[which@2.0.2](https://github.com/isaacs/node-which) - ISC
[word-wrap@1.2.5](https://github.com/jonschlinkert/word-wrap) - MIT
[yallist@3.1.1](https://github.com/isaacs/yallist) - ISC
[yaml-ast-parser@0.0.43](https://github.com/mulesoft-labs/yaml-ast-parser) - Apache-2.0
[yargs-parser@21.1.1](https://github.com/yargs/yargs-parser) - ISC
[yocto-queue@0.1.0](https://github.com/sindresorhus/yocto-queue) - MIT

## Roles

- **runtime**: fastapi/uvicorn/pydantic/numpy/nvidia-ml-py/prometheus-client
  (API); torch/triton/nvidia-* (engine); UI runtime deps.
- **validation only**: TexasSolver and the Phase 7/8 cross-check scripts.
- **dev only**: pytest, httpx, matplotlib, eslint, playwright, vite,
  typescript, openapi-typescript, and other devDependencies.
