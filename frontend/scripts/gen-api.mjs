// Regenerates src/api/openapi.json and src/api/schema.d.ts from the Python backend,
// so the frontend's request/response types always match the API. Run: npm run gen:api
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const python = resolve(root, "../.venv/bin/python");
const json = resolve(root, "src/api/openapi.json");

execFileSync(
  python,
  [
    "-c",
    "import json,sys;from grammarguard.api.main import create_app;from grammarguard.api.settings import Settings;" +
      "app=create_app(Settings(warm_start=False));json.dump(app.openapi(),open(sys.argv[1],'w'),indent=1)",
    json,
  ],
  { cwd: resolve(root, ".."), stdio: "inherit" },
);
execFileSync("npx", ["openapi-typescript", json, "-o", resolve(root, "src/api/schema.d.ts")], { cwd: root, stdio: "inherit" });
