// TEST DOUBLE ONLY: a local stand-in for Slack incoming webhooks and the Jira
// REST v3 endpoints OpsRelay uses. Lets the smoke test exercise plugin code
// paths without real credentials. Never used by the running app.
import http from "node:http";

export interface FakeState {
  slack: string[];
  issues: { key: string; labels: string[]; summary: string; comments: string[] }[];
  creates: number;
}

export function startFakePlugins(port: number) {
  const state: FakeState = { slack: [], issues: [], creates: 0 };
  const server = http.createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {};
    const send = (code: number, obj: unknown) => {
      res.writeHead(code, { "content-type": "application/json" });
      res.end(JSON.stringify(obj));
    };
    const url = req.url || "";
    if (url === "/slack" && req.method === "POST") {
      state.slack.push(body.text);
      res.writeHead(200);
      return res.end("ok");
    }
    if (url === "/jira/rest/api/3/myself") return send(200, { displayName: "Fake Jira" });
    if (url === "/jira/rest/api/3/search/jql") {
      const label = /labels = "([^"]+)"/.exec(body.jql)?.[1];
      return send(200, { issues: state.issues.filter((i) => i.labels.includes(label!)).map((i) => ({ key: i.key })) });
    }
    if (url === "/jira/rest/api/3/issue" && req.method === "POST") {
      state.creates++;
      const key = `OPS-${state.issues.length + 1}`;
      state.issues.push({ key, labels: body.fields.labels, summary: body.fields.summary, comments: [] });
      return send(201, { key });
    }
    const m = /^\/jira\/rest\/api\/3\/issue\/([^/]+)\/comment/.exec(url);
    if (m) {
      const issue = state.issues.find((i) => i.key === m[1]);
      if (!issue) return send(404, {});
      if (req.method === "POST") {
        issue.comments.push(body.body.content[0].content[0].text);
        return send(201, { id: String(issue.comments.length) });
      }
      return send(200, { comments: issue.comments.map((t, i) => ({ id: String(i), body: { content: [{ content: [{ text: t }] }] } })) });
    }
    send(404, { url });
  });
  server.listen(port);
  return { state, close: () => server.close() };
}
