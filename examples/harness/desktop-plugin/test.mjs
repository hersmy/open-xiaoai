import { test } from 'node:test';
import assert from 'node:assert/strict';
import { authorized, finalText, start, TURN_TIMEOUT_MS } from './index.js';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
test('requires exact bearer token; rejects missing or wrong length', () => {
  assert.equal(authorized('Bearer correct', 'correct'), true);
  for (const wrong of [undefined, '', 'correct', 'Bearer bad']) assert.equal(authorized(wrong, 'correct'), false);
});
test('desktop tool turns have a thirty-minute deadline', () => {
  assert.equal(TURN_TIMEOUT_MS, 1800000);
});
test('never speaks reasoning or tool-call intermediate message', () => {
  const event = content => ({ type: 'assistant/message', data: { message: { content } } });
  assert.equal(finalText(event([{ type: 'reasoning', text: 'private' }])), null);
  assert.equal(finalText(event([{ type: 'text', text: 'working' }, { type: 'tool-call' }])), null);
  assert.equal(finalText(event([{ type: 'reasoning', text: 'private' }, { type: 'text', text: 'answer' }])), 'answer');
});
test('desktop plugin creates a scoped session and returns only completed final answer', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'xiaoai-plugin-'));
  const emitter = new EventEmitter();
  const workspace = { id: 'workspace-test', sessionIds: [] };
  const agent = { id: 'voice-test', status: 'idle', cancel() {} };
  let dispose, created, prompted;
  const ctx = {
    logger: { info() {} },
    workspaceRegistry: { async create() { return workspace; } },
    sessionController: {
      async create(req) { created = req; workspace.sessionIds.push(agent.id); return { sessionId: agent.id }; },
      async rename() {},
      async resolveAgent() { return { agent }; },
      async prompt(req) {
        prompted = req;
        queueMicrotask(() => {
          emitter.emit('session/event', { id: agent.id }, { type: 'assistant/message', data: { message: {
            content: [{ type: 'reasoning', text: 'hidden' }, { type: 'text', text: '桌面回答' }] } } });
          emitter.emit('session/event', { id: agent.id }, { type: 'turn/end', data: { reason: { kind: 'completed' } } });
        });
      },
    },
    on(event, fn) { emitter.on(event, fn); return () => emitter.off(event, fn); },
    effect(fn) { dispose = fn(); },
  };
  try {
    await start(ctx, dir, '/project');
    const { url, token } = JSON.parse(await readFile(path.join(dir, 'desktop.json'), 'utf8'));
    assert.equal((await fetch(url + '/health')).status, 403);
    const response = await fetch(url + '/ask', { method: 'POST',
      headers: { Authorization: 'Bearer ' + token, 'content-type': 'application/json' }, body: JSON.stringify({ text: '你好' }) });
    assert.equal(response.status, 200);
    const data = await response.json();
    assert.equal(data.answer, '桌面回答');
    assert.equal(created.workspaceId, workspace.id);
    assert.equal(prompted.sessionId, agent.id);
    assert.equal(emitter.listenerCount('session/event'), 0);
  } finally {
    await dispose?.();
    await rm(dir, { recursive: true, force: true });
  }
});
