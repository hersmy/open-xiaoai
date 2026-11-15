import http from 'node:http';
import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { mkdir, readFile, writeFile, rename, unlink } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export const name = 'xiaoai-voice';
export const inject = ['agents', 'sessionController', 'workspaceRegistry'];
export const TURN_TIMEOUT_MS = 30 * 60 * 1000;
const runtime = fileURLToPath(new URL('../.runtime/', import.meta.url));
const project = '/Volumes/PHILIPS/小米音箱';

export function authorized(value, secret) {
  const a = Buffer.from(value || '');
  const b = Buffer.from('Bearer ' + secret);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function finalText(event) {
  if (event.type !== 'assistant/message') return null;
  const content = event.data?.message?.content;
  if (!Array.isArray(content) || content.some(p => p.type === 'tool-call')) return null;
  const text = content.filter(p => p.type === 'text').map(p => p.text).join('');
  return text.trim() || null;
}

export async function apply(ctx) {
  return start(ctx, runtime, project);
}

export async function start(ctx, runtime, project) {
  await mkdir(runtime, { recursive: true, mode: 0o700 });
  const descriptor = path.join(runtime, 'desktop.json');
  const stateFile = path.join(runtime, 'desktop-session.json');
  let sessionId = null;
  try { sessionId = JSON.parse(await readFile(stateFile, 'utf8')).session_id; }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const token = randomBytes(32).toString('hex');
  let busy = false;
  let activeReject;
  async function ensure() {
    const workspace = await ctx.workspaceRegistry.create(project, '小米音箱');
    if (!sessionId) {
      const created = await ctx.sessionController.create({ workspaceId: workspace.id });
      sessionId = created.sessionId;
      await ctx.sessionController.rename({ sessionId, title: '大爱同学 · 语音对话' });
      const tmp = stateFile + '.tmp';
      await writeFile(tmp, JSON.stringify({ session_id: sessionId }) + '\n', { mode: 0o600 });
      await rename(tmp, stateFile);
    } else if (!workspace.sessionIds.includes(sessionId)) {
      // Never adopt an arbitrary existing research session.
      throw new Error('Voice session does not belong to 小米音箱 workspace');
    }
    return sessionId;
  }
  async function ask(text, signal) {
    if (busy) throw new Error('Voice session busy');
    busy = true;
    let off, offError, timer;
    let cancel;
    try {
      const id = await ensure();
      const resolved = await ctx.sessionController.resolveAgent(id);
      if (resolved.error) throw resolved.error;
      if (resolved.agent.status === 'running') throw new Error('Session is already running from another input');
      let answer = '';
      const tools = [];
      const done = new Promise((resolve, reject) => {
        activeReject = reject;
        off = ctx.on('session/event', (session, event) => {
          if (session.id !== id) return;
          const prose = finalText(event);
          if (prose) answer = prose;
          if (event.type === 'tool/call') tools.push(event.data.name);
          if (event.type === 'turn/end') {
            if (event.data.reason?.kind === 'completed' && answer) resolve({ answer, session_id: id, tools });
            else reject(new Error('Desktop Agent turn did not complete with a final answer'));
          }
        }, { global: true });
        offError = ctx.on('agent/error', ({ agent, error }) => {
          if (agent.id === id) reject(error);
        }, { global: true });
        cancel = () => {
          resolved.agent.cancel({ kind: 'user' }, { keepInbox: false });
          reject(new Error('VOICE_CANCELLED: 语音任务已中断'));
        };
        signal.addEventListener('abort', cancel, { once: true });
        timer = setTimeout(() => {
          // Report our own deadline rather than mislabel it as a human cancel.
          reject(new Error('VOICE_TIMEOUT: 任务已达到三十分钟上限，请在 Harness 查看执行情况'));
          resolved.agent.cancel({ kind: 'user' }, { keepInbox: false });
        }, TURN_TIMEOUT_MS);
      });
      // Attach a rejection handler before awaiting prompt admission.
      done.catch(() => {});
      await ctx.sessionController.prompt({ sessionId: id, requestId: randomUUID(),
        clientTimeZone: 'Asia/Shanghai', content: [{ type: 'text', text:
          '这是小米音箱的语音输入。请按现有模型、工具和权限处理；回复会朗读，请用简洁自然中文，避免表格和代码块。\n用户说：' + text }] }, signal);
      return await done;
    } finally {
      clearTimeout(timer);
      off?.(); offError?.();
      if (cancel) signal.removeEventListener('abort', cancel);
      busy = false; activeReject = null;
    }
  }
  const server = http.createServer(async (req, res) => {
    const json = (status, value) => {
      if (res.destroyed) return;
      res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
      res.end(JSON.stringify(value));
    };
    if (!authorized(req.headers.authorization, token) || req.headers.origin) {
      json(403, { error: 'Unauthorized' }); return;
    }
    const abort = new AbortController();
    res.on('close', () => { if (!res.writableEnded) abort.abort(); });
    try {
      if (req.method === 'GET' && req.url === '/health') {
        json(200, { ready: true, session_id: sessionId, workspace: project, busy,
                    version: '0.2.0', turn_timeout_ms: TURN_TIMEOUT_MS }); return;
      }
      if (req.method !== 'POST' || req.url !== '/ask') { json(404, { error: 'Not found' }); return; }
      let size = 0, chunks = [];
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 32768) { json(413, { error: 'Too large' }); return; }
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString());
      if (typeof body.text !== 'string' || !body.text.trim() || body.text.length > 8000) {
        json(400, { error: 'Invalid text' }); return;
      }
      json(200, await ask(body.text, abort.signal));
    } catch (error) { json(502, { error: String(error.message || error) }); }
  });
  await ensure();
  await new Promise((resolve, reject) => {
    server.once('error', reject); server.listen(0, '127.0.0.1', resolve);
  });
  const port = server.address().port;
  const tmp = descriptor + '.tmp';
  await writeFile(tmp, JSON.stringify({ url: 'http://127.0.0.1:' + port, token }) + '\n', { mode: 0o600 });
  await rename(tmp, descriptor);
  ctx.effect(() => async () => {
    activeReject?.(new Error('Desktop plugin unloaded'));
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await unlink(descriptor).catch(() => {});
  });
  ctx.logger.info('小米音箱桌面语音会话就绪（仅本机连接）');
}
