import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const EXTENSION_DIR = dirname(fileURLToPath(import.meta.url));
const LOGGRAPH_ROOT = process.env.LOGGRAPH_ROOT ?? resolve(EXTENSION_DIR, "..", "..");
const LOGGRAPH_SHIM = join(LOGGRAPH_ROOT, "loggraph-shim.js");

function normalizePath(cwd: string, value: string): string {
  const raw = value.startsWith("@") ? value.slice(1) : value;
  return resolve(cwd, raw);
}

function projectIndexPath(project: string): string {
  return join(project, ".loggraph", "index.json");
}

async function runLogGraph(
  pi: ExtensionAPI,
  args: string[],
  cwd: string,
  signal?: AbortSignal,
  timeout = 180_000,
) {
  if (!existsSync(LOGGRAPH_SHIM)) {
    throw new Error(`LogGraph shim not found: ${LOGGRAPH_SHIM}`);
  }
  const result = await pi.exec("node", [LOGGRAPH_SHIM, ...args], {
    cwd: LOGGRAPH_ROOT,
    signal,
    timeout,
    env: process.env,
  });
  if (result.code !== 0) {
    throw new Error(`loggraph failed (${result.code})\nSTDOUT:\n${result.stdout}\nSTDERR:\n${result.stderr}`);
  }
  return result.stdout.trim();
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "loggraph_init",
    label: "LogGraph Init",
    description: "Initialize a LogGraph cache for a project. This scans source code once and writes <project>/.loggraph/index.json.",
    promptSnippet: "Initialize a LogGraph code/log-site cache for a project.",
    promptGuidelines: [
      "Use loggraph_init before loggraph_analyze when the project has no .loggraph/index.json cache or source changed.",
      "Use loggraph_analyze for log files after a LogGraph cache exists instead of writing ad-hoc parsing scripts.",
    ],
    parameters: Type.Object({
      project: Type.String({ description: "Project root directory." }),
      src: Type.Optional(Type.String({ description: "Source directory to index. Defaults to project root." })),
      out: Type.Optional(Type.String({ description: "Index output path. Defaults to <project>/.loggraph/index.json." })),
    }),
    async execute(_toolCallId, params, signal, _onUpdate, ctx) {
      const project = normalizePath(ctx.cwd, params.project);
      const args = ["init", project];
      if (params.src) args.push("--src", normalizePath(ctx.cwd, params.src));
      if (params.out) args.push("--out", normalizePath(ctx.cwd, params.out));
      const stdout = await runLogGraph(pi, args, ctx.cwd, signal);
      return { content: [{ type: "text", text: stdout }], details: { stdout } };
    },
  });

  pi.registerTool({
    name: "loggraph_analyze",
    label: "LogGraph Analyze",
    description: "Analyze a log file using an existing LogGraph cache and produce a report with source candidates and domain findings.",
    promptSnippet: "Analyze logs using a prebuilt LogGraph cache.",
    promptGuidelines: [
      "Use loggraph_analyze to inspect logs against a cached code graph, rather than grep/find or one-off Python scripts.",
      "If loggraph_analyze says the index is missing, run loggraph_init first.",
    ],
    parameters: Type.Object({
      project: Type.String({ description: "Project root directory containing .loggraph/index.json." }),
      logFile: Type.String({ description: "Log file to analyze." }),
      index: Type.Optional(Type.String({ description: "Index cache path. Defaults to <project>/.loggraph/index.json." })),
      out: Type.Optional(Type.String({ description: "Analysis report output path." })),
      top: Type.Optional(Type.Number({ description: "Top candidates per log line.", default: 3 })),
      showMatches: Type.Optional(Type.Number({ description: "How many matched lines to show in the compact result.", default: 10 })),
      allLines: Type.Optional(Type.Boolean({ description: "Analyze all log lines instead of app-tag lines only.", default: false })),
    }),
    async execute(_toolCallId, params, signal, _onUpdate, ctx) {
      const project = normalizePath(ctx.cwd, params.project);
      const index = params.index ? normalizePath(ctx.cwd, params.index) : projectIndexPath(project);
      if (!existsSync(index)) {
        throw new Error(`LogGraph index not found: ${index}. Run loggraph_init first.`);
      }
      const args = [
        "analyze",
        project,
        "--log-file",
        normalizePath(ctx.cwd, params.logFile),
        "--index",
        index,
        "--top",
        String(params.top ?? 3),
        "--show-matches",
        String(params.showMatches ?? 10),
      ];
      if (params.out) args.push("--out", normalizePath(ctx.cwd, params.out));
      if (params.allLines) args.push("--all-lines");
      const stdout = await runLogGraph(pi, args, ctx.cwd, signal);
      return { content: [{ type: "text", text: stdout }], details: { stdout } };
    },
  });

  pi.registerTool({
    name: "loggraph_query",
    label: "LogGraph Query",
    description: "Query one log line/message against an existing LogGraph index and return ranked source candidates.",
    parameters: Type.Object({
      index: Type.String({ description: "Path to .loggraph/index.json." }),
      log: Type.String({ description: "One log line or message." }),
      top: Type.Optional(Type.Number({ default: 3 })),
    }),
    async execute(_toolCallId, params, signal, _onUpdate, ctx) {
      const args = ["query", normalizePath(ctx.cwd, params.index), "--log", params.log, "--top", String(params.top ?? 3)];
      const stdout = await runLogGraph(pi, args, ctx.cwd, signal);
      return { content: [{ type: "text", text: stdout }], details: { stdout } };
    },
  });

  pi.registerCommand("loggraph-init", {
    description: "Initialize LogGraph cache: /loggraph-init <project> [src]",
    handler: async (args, ctx) => {
      const [projectArg, srcArg] = args.trim().split(/\s+/).filter(Boolean);
      if (!projectArg) {
        ctx.ui.notify("Usage: /loggraph-init <project> [src]", "error");
        return;
      }
      const project = normalizePath(ctx.cwd, projectArg);
      const cliArgs = ["init", project];
      if (srcArg) cliArgs.push("--src", normalizePath(ctx.cwd, srcArg));
      const stdout = await runLogGraph(pi, cliArgs, ctx.cwd, undefined);
      ctx.ui.notify(stdout, "info");
    },
  });

  pi.registerCommand("loggraph-analyze", {
    description: "Analyze log with cached LogGraph: /loggraph-analyze <project> <log-file>",
    handler: async (args, ctx) => {
      const [projectArg, logFileArg] = args.trim().split(/\s+/).filter(Boolean);
      if (!projectArg || !logFileArg) {
        ctx.ui.notify("Usage: /loggraph-analyze <project> <log-file>", "error");
        return;
      }
      const project = normalizePath(ctx.cwd, projectArg);
      const index = projectIndexPath(project);
      if (!existsSync(index)) {
        ctx.ui.notify(`Missing index: ${index}. Run /loggraph-init first.`, "error");
        return;
      }
      const stdout = await runLogGraph(pi, ["analyze", project, "--log-file", normalizePath(ctx.cwd, logFileArg), "--index", index], ctx.cwd, undefined);
      ctx.ui.notify(stdout, "info");
    },
  });
}
