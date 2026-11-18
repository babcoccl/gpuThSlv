import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";

// Contract guard: every API call must go through the generated openapi-fetch
// client (src/api/client.ts). Raw fetch( is banned outside that module;
// EventSource (SSE) is the only other transport and lives in src/api/sse.ts.
export default [
  {
    files: ["src/**/*.ts", "src/**/*.tsx", "tests/**/*.ts"],
    languageOptions: { parser: tsParser },
    plugins: { "@typescript-eslint": tsPlugin },
    rules: {
      "no-restricted-globals": [
        "error",
        {
          name: "fetch",
          message: "Route API calls through the generated client in src/api/client.ts.",
        },
      ],
      "no-restricted-syntax": [
        "error",
        {
          selector: "CallExpression[callee.name='fetch'], CallExpression[callee.object.name='window'][callee.property.name='fetch']",
          message: "Route API calls through the generated client in src/api/client.ts.",
        },
        {
          selector: "NewExpression[callee.name='EventSource']",
          message: "SSE lives in src/api/sse.ts only.",
        },
        {
          selector: "TSTypeReference[typeName.name='XMLHttpRequest'], NewExpression[callee.name='XMLHttpRequest']",
          message: "Route API calls through the generated client in src/api/client.ts.",
        },
      ],
    },
  },
  {
    files: ["src/api/client.ts"],
    rules: {
      "no-restricted-globals": "off",
      "no-restricted-syntax": [
        "error",
        {
          selector: "NewExpression[callee.name='EventSource']",
          message: "SSE lives in src/api/sse.ts only.",
        },
      ],
    },
  },
  {
    files: ["src/api/sse.ts"],
    rules: {
      "no-restricted-globals": ["error", { name: "fetch", message: "Use client.ts." }],
      "no-restricted-syntax": [
        "error",
        {
          selector: "CallExpression[callee.name='fetch'], CallExpression[callee.object.name='window'][callee.property.name='fetch']",
          message: "Route API calls through the generated client in src/api/client.ts.",
        },
        {
          selector: "NewExpression[callee.name='XMLHttpRequest']",
          message: "Route API calls through the generated client in src/api/client.ts.",
        },
      ],
    },
  },
];
