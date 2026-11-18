// Transport: every call is POST /api/<action> with the session token from the launch URL.

function remembered() {
  try {
    return sessionStorage.getItem("studio-session") || "";
  } catch {
    return "";
  }
}

const fragment = location.hash.slice(1);
export const token = fragment || remembered();
if (fragment) {
  try {
    sessionStorage.setItem("studio-session", fragment);
  } catch {
    // Private windows may refuse storage; the token still works until this tab reloads.
  }
  history.replaceState(null, "", location.pathname);
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(action, data = {}, signal) {
  let response;
  try {
    response = await fetch("/api/" + action, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(data),
      signal,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("Studio did not answer. The local server may have stopped.", 0);
  }
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    throw new ApiError(
      failure.error ||
        (response.status === 403
          ? "This tab's session is no longer valid."
          : "Studio could not complete the request."),
      response.status,
    );
  }
  return response.json();
}
