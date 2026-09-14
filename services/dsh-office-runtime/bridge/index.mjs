import { randomUUID } from "node:crypto";
import { createInterface } from "node:readline";

import { installModelSelection } from "@deepseek-ai/dsh-agent";
import { createUserMessage } from "@deepseek-ai/dsh-llm";
import { SessionId } from "@deepseek-ai/dsh-session";
import { defineTool } from "@deepseek-ai/dsh-tools";

import { setRuntimeCredential } from "./credentials.mjs";

export const name = "fsv-office-bridge";
export const inject = ["agents", "sessions", "systemPrompt", "tools"];

const PROTOCOL = "fsv-office/1";
const PROVIDER = "fsv-office";
const API_KEY_REF = "FSV_OFFICE_API_KEY";
const EXIT_GRACE_MS = 1000;
// 桌宠能力的工具调用要等主进程执行完再回包；主进程只做事件分发（音乐是异步搜索），
// 正常情况下毫秒级返回，超时说明桌面进程已经掉线。
const PET_TOOL_TIMEOUT_MS = 15000;
const EFFORTS = new Set(["off", "low", "high", "max", "ultra"]);
const EFFORT_STRATEGIES = {
  off: "Use a direct execution strategy. Keep planning lightweight and perform only the checks needed for a correct, safe result.",
  low: "Use a fast execution strategy. Keep planning minimal, change the smallest coherent set of files, and check only what the change touches.",
  high: "Use a balanced execution strategy. Inspect relevant context, maintain a short plan for multi-step work, and verify material changes.",
  max: "Use a thorough execution strategy. Investigate relevant alternatives and edge cases, keep progress explicit, and verify the result comprehensively.",
  ultra: "Use a deliberate execution strategy. Map the request onto the codebase before editing, weigh alternatives and failure modes, keep an explicit plan, and verify the result against edge cases and regressions.",
};

const COUNT_PARAMETER = {
  type: "integer",
  description: "生成数量，未指定时为 1。",
};

// 办公模式开放的桌宠工具：名称与参数必须和 lib/script/office/pet_tools.py 的
// PET_TOOL_NAMES 一致（同一套工具名，两种模式的桌宠能力才是同一个契约）。
// 执行体在主进程：这里只回传 (name, arguments)，等 pet_tool_result 命令返回结果。
const PET_TOOLS = [
  {
    name: "play_music",
    description: "仅当用户明确要求播放音乐时调用。按歌名搜索并播放；未指定歌名时可省略 query。",
    parameters: { query: { type: "string", description: "用户要求播放的歌名。" } },
  },
  {
    name: "next_track",
    description: "仅当用户明确要求下一曲时调用。",
    parameters: {},
  },
  {
    name: "toggle_play_pause",
    description: "仅当用户明确要求播放或暂停切换时调用。",
    parameters: {},
  },
  {
    name: "spawn_snow_leopard",
    description: "仅当用户明确要求生成雪豹时调用。",
    parameters: { count: COUNT_PARAMETER },
  },
  {
    name: "spawn_sofa",
    description: "仅当用户明确要求生成沙发时调用。",
    parameters: { count: COUNT_PARAMETER },
  },
  {
    name: "spawn_motorcycle",
    description: "仅当用户明确要求生成摩托时调用。",
    parameters: { count: COUNT_PARAMETER },
  },
  {
    name: "start_timer",
    description: "仅当用户明确要求倒计时或闹钟时调用。",
    parameters: { seconds: { type: "integer", description: "倒计时总秒数，未指定时为 30。" } },
  },
  {
    name: "set_volume",
    description: "仅当用户明确要求把音量设为某个值时调用。",
    parameters: { percent: { type: "number", required: true, description: "目标音量百分比（0-100）。" } },
  },
  {
    name: "change_volume",
    description: "仅当用户明确要求调高或调低音量时调用。",
    parameters: {
      delta_percent: { type: "number", required: true, description: "音量百分比变化量（-100 到 100）。" },
    },
  },
  {
    name: "teleport_pet",
    description: "仅当用户明确要求桌宠瞬移时调用。x/y 都在 0 到 1 之间，1 表示左或上，0 表示右或下。",
    parameters: {
      x: { type: "number", required: true, description: "水平位置。" },
      y: { type: "number", required: true, description: "垂直位置。" },
    },
  },
];

function emit(type, payload = {}) {
  process.stdout.write(JSON.stringify({ protocol: PROTOCOL, type, ...payload }) + "\n");
}

function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

function userMessage(text) {
  return createUserMessage({
    content: [{ type: "text", text }],
    source: { kind: "user" },
  });
}

function normalizeEffort(value) {
  const effort = String(value ?? "high").trim().toLowerCase();
  if (!EFFORTS.has(effort)) throw new Error(`unsupported reasoning effort: ${effort}`);
  return effort;
}

function normalizeCommand(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("JSONL command must be an object");
  }
  const type = String(value.type ?? "").trim();
  if (!type) throw new TypeError("JSONL command type cannot be empty");
  return { ...value, type };
}

export function apply(ctx) {
  const tasks = new Map();
  const sessionTasks = new Map();
  const pendingApprovals = new Map();
  const pendingPetTools = new Map();
  const toolCalls = new Map();
  const pendingCommands = [];
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  const exit = ctx.get("appExit");
  let commandChain = Promise.resolve();
  let configured = false;
  let closing = false;
  let ready = false;

  if (typeof exit !== "function") {
    throw new Error("fsv-office-bridge requires the launcher appExit service");
  }

  function requestExit(code) {
    setTimeout(() => process.exit(code), EXIT_GRACE_MS);
    exit(code);
  }

  const findTaskForAgent = (agent) => sessionTasks.get(String(agent?.session?.id ?? ""));

  /** 把一次桌宠工具调用回传主进程，并等 pet_tool_result 命令带回执行结果。 */
  function requestPetTool(name, args, exec) {
    const taskId = findTaskForAgent(exec.agent);
    if (!taskId) throw new Error(`桌宠工具 ${name} 需要一个正在运行的办公任务`);
    const callId = randomUUID();
    return new Promise((resolve, reject) => {
      let settled = false;
      let timer;
      const finish = (failure, result) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        pendingPetTools.delete(callId);
        if (failure) reject(failure);
        else resolve(result);
      };
      timer = setTimeout(
        () => finish(new Error(`桌宠工具 ${name} 等待桌面进程响应超时`)),
        PET_TOOL_TIMEOUT_MS
      );
      pendingPetTools.set(callId, finish);
      emit("pet_tool_call", { taskId, callId, name, arguments: args });
    });
  }

  for (const spec of PET_TOOLS) {
    ctx.tools.register(defineTool({
      name: spec.name,
      description: spec.description,
      parameters: spec.parameters,
      output: {
        schema: {
          type: "object",
          additionalProperties: false,
          properties: { message: { type: "string", required: true } },
        },
        render: (_args, value) => [{ type: "text", text: value.message }],
      },
      async execute(args, exec) {
        const result = await requestPetTool(spec.name, args, exec);
        if (!result.ok) throw new Error(result.message || `桌宠工具 ${spec.name} 执行失败`);
        return { message: result.message };
      },
    }));
  }

  ctx.on("session/event", (session, event) => {
    const sessionId = String(session?.id ?? "");
    const taskId = sessionTasks.get(sessionId);
    if (!taskId) return;
    if (event.type === "tool/call") {
      toolCalls.set(`${sessionId}:${event.data.callId}`, {
        name: event.data.name,
        arguments: event.data.arguments,
      });
    }
    emit("session_event", {
      taskId,
      sessionId,
      event: {
        seq: event.seq,
        time: event.time,
        type: event.type,
        data: event.data,
      },
    });
  });

  ctx.on("approval/request", async (request, next) => {
    const taskId = findTaskForAgent(request.agent);
    if (!taskId) return next();
    const approvalId = randomUUID();
    const sessionId = String(request.agent.session.id);
    const call = request.callId
      ? toolCalls.get(`${sessionId}:${request.callId}`)
      : undefined;
    return new Promise((resolve) => {
      let settled = false;
      const finish = (outcome) => {
        if (settled) return;
        settled = true;
        request.signal?.removeEventListener("abort", onAbort);
        pendingApprovals.delete(approvalId);
        resolve(outcome);
      };
      const onAbort = () => finish("cancelled");
      request.signal?.addEventListener("abort", onAbort, { once: true });
      pendingApprovals.set(approvalId, finish);
      emit("approval_request", {
        taskId,
        sessionId,
        approvalId,
        toolName: request.toolName,
        callId: request.callId,
        reason: request.reason,
        command: call,
      });
    });
  });

  async function monitorIdle(taskId, record, epoch) {
    try {
      await record.handle.agent.whenIdle();
      if (record.epoch !== epoch || closing) return;
      await ctx.sessions.flush(record.handle.agent.session);
      emit("task_idle", { taskId, sessionId: record.sessionId });
    } catch (error) {
      emit("task_error", { taskId, message: errorText(error) });
    }
  }

  function selectionFor(command) {
    const model = String(command.model ?? process.env.FSV_OFFICE_MODEL ?? "").trim();
    if (!model) throw new Error("office model cannot be empty");
    return {
      provider: PROVIDER,
      model,
    };
  }

  async function createTask(command, resume) {
    if (!configured) throw new Error("office runtime has not received credentials");
    const taskId = String(command.taskId ?? "").trim();
    const workspace = String(command.workspace ?? "").trim();
    const prompt = String(command.prompt ?? "").trim();
    if (!taskId || !workspace) throw new Error("taskId and workspace are required");
    if (tasks.has(taskId)) {
      if (!resume) throw new Error(`task already exists: ${taskId}`);
      const record = tasks.get(taskId);
      emit("task_created", { taskId, sessionId: record.sessionId, resumed: true });
      if (prompt) await followup({ taskId, text: prompt });
      return;
    }

    const sessionId = String(
      command.sessionId ?? `fsv-office-${taskId}-${randomUUID()}`
    ).trim();
    const selection = selectionFor(command);
    const selectionRef = { current: selection, assembled: undefined };
    const effortRef = { current: normalizeEffort(command.reasoningEffort) };
    const sharedOptions = {
      agentOptions: { provider: selection.provider, model: selection.model },
      setup: (agentCtx) => {
        installModelSelection(agentCtx, selectionRef);
        agentCtx.systemPrompt.variable(
          "fsv_office_reasoning_strategy",
          () => EFFORT_STRATEGIES[effortRef.current]
        );
        agentCtx.systemPrompt.section({
          name: "fsv:reasoning-strategy",
          order: 10,
          text: "{{fsv_office_reasoning_strategy}}",
        });
      },
    };

    const handle = resume
      ? await ctx.agents.resume({
          ...sharedOptions,
          resumeSessionId: SessionId(sessionId),
        })
      : await ctx.agents.create({
          ...sharedOptions,
          sessionId: SessionId(sessionId),
          meta: { cwd: workspace },
        });
    const record = { handle, selectionRef, effortRef, sessionId, epoch: 0 };
    tasks.set(taskId, record);
    sessionTasks.set(sessionId, taskId);
    await handle.agent.whenIdle();
    emit("task_created", { taskId, sessionId, resumed: Boolean(resume) });
    if (prompt) {
      record.epoch += 1;
      handle.agent.followup(userMessage(prompt));
      void monitorIdle(taskId, record, record.epoch);
    }
  }

  async function followup(command) {
    const taskId = String(command.taskId ?? "").trim();
    const text = String(command.text ?? "").trim();
    const record = tasks.get(taskId);
    if (!record) throw new Error(`unknown live task: ${taskId}`);
    if (!text) throw new Error("followup text cannot be empty");
    record.epoch += 1;
    record.handle.agent.followup(userMessage(text));
    void monitorIdle(taskId, record, record.epoch);
  }

  async function handleCommand(raw) {
    const command = normalizeCommand(raw);
    switch (command.type) {
      case "configure": {
        const apiKey = String(command.apiKey ?? "").trim();
        if (!apiKey) throw new Error("office API key cannot be empty");
        setRuntimeCredential(API_KEY_REF, apiKey);
        configured = true;
        emit("configured");
        return;
      }
      case "create":
        await createTask(command, false);
        return;
      case "resume":
        await createTask(command, true);
        return;
      case "followup":
        await followup(command);
        return;
      case "cancel": {
        const taskId = String(command.taskId ?? "").trim();
        const record = tasks.get(taskId);
        if (!record) throw new Error(`unknown live task: ${taskId}`);
        record.epoch += 1;
        record.handle.agent.cancel({ kind: "user" });
        emit("task_cancelled", { taskId, sessionId: record.sessionId });
        return;
      }
      case "set_reasoning": {
        const taskId = String(command.taskId ?? "").trim();
        const record = tasks.get(taskId);
        if (!record) throw new Error(`unknown live task: ${taskId}`);
        record.effortRef.current = normalizeEffort(command.reasoningEffort);
        emit("reasoning_changed", {
          taskId,
          reasoningEffort: record.effortRef.current,
        });
        return;
      }
      case "approval": {
        const approvalId = String(command.approvalId ?? "").trim();
        const outcome = String(command.outcome ?? "").trim();
        if (outcome !== "allowed-once" && outcome !== "rejected") {
          throw new Error(`invalid approval outcome: ${outcome}`);
        }
        const resolve = pendingApprovals.get(approvalId);
        if (!resolve) throw new Error(`unknown approval: ${approvalId}`);
        resolve(outcome);
        return;
      }
      case "pet_tool_result": {
        const callId = String(command.callId ?? "").trim();
        const resolve = pendingPetTools.get(callId);
        // 未知 callId 只可能是超时后才回来的结果：忽略，不能报错（command_error 会让任务失败）。
        if (!resolve) return;
        resolve(undefined, {
          ok: command.ok !== false,
          message: String(command.message ?? ""),
        });
        return;
      }
      case "shutdown":
        await shutdown();
        return;
      default:
        throw new Error(`unknown command type: ${command.type}`);
    }
  }

  async function shutdown() {
    if (closing) return;
    closing = true;
    for (const resolve of pendingApprovals.values()) resolve("cancelled");
    pendingApprovals.clear();
    for (const finish of pendingPetTools.values()) {
      finish(new Error("办公运行时正在退出"));
    }
    pendingPetTools.clear();
    await Promise.allSettled([...tasks.values()].map((record) => record.handle.dispose()));
    tasks.clear();
    sessionTasks.clear();
    emit("shutdown_complete");
    input.close();
    process.stdin.destroy();
    requestExit(0);
  }

  function dispatch(command) {
    commandChain = commandChain
      .then(() => handleCommand(command))
      .catch((error) => {
        emit("command_error", {
          requestId: command?.requestId,
          taskId: command?.taskId,
          command: command?.type,
          message: errorText(error),
        });
      });
  }

  input.on("line", (line) => {
    const text = String(line ?? "").trim();
    if (!text) return;
    let command;
    try {
      command = JSON.parse(text);
    } catch (error) {
      emit("protocol_error", { message: `invalid JSON: ${errorText(error)}` });
      return;
    }
    if (ready) dispatch(command);
    else pendingCommands.push(command);
  });

  input.on("close", () => {
    if (closing) return;
    const command = { type: "shutdown" };
    if (ready) dispatch(command);
    else pendingCommands.push(command);
  });

  async function start() {
    await ctx.get("loader")?.await();
    ready = true;
    emit("ready", { pid: process.pid });
    for (const command of pendingCommands.splice(0)) dispatch(command);
  }

  void start().catch((error) => {
    emit("fatal", { message: errorText(error) });
    input.close();
    process.stdin.destroy();
    requestExit(1);
  });
}
