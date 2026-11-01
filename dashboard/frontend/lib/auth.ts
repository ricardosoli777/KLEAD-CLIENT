export function getAuthToken(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/(?:^|;\s*)klead_token=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export function getAuthFuncao(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/(?:^|;\s*)klead_funcao=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export function clearAuthToken() {
  document.cookie = "klead_token=; path=/; max-age=0";
  document.cookie = "klead_funcao=; path=/; max-age=0";
}
