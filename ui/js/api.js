import {beginRequest, finishRequest} from "./activity-console.js";
let csrfToken = "";

async function parseResponse(response) {
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`로컬 앱 응답을 해석하지 못했습니다. (${response.status})`);
  }
  if (!response.ok || payload.ok === false) {
    const error = payload?.error || {};
    const value = new Error(error.message || `요청 실패 (${response.status})`);
    value.code = error.code;
    value.jobId = error.job_id;
    throw value;
  }
  return payload;
}

export async function bootstrap() {
  const payload = await parseResponse(await fetch("/api/v1/bootstrap", { cache: "no-store" }));
  csrfToken = payload.csrf_token;
  return payload;
}

export async function get(path) {
  return parseResponse(await fetch(path, { cache: "no-store" }));
}

export async function post(path, body = {}) {
  const activity = beginRequest(path, body);
  try {
    const result = await parseResponse(await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-StarMode-Token": csrfToken,
      },
      body: JSON.stringify(body),
    }));
    finishRequest(activity, result);
    return result;
  } catch (error) {
    finishRequest(activity, null, error);
    throw error;
  }
}

export function hasToken() {
  return Boolean(csrfToken);
}
