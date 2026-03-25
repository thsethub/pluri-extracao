const decodeBasicHtmlEntities = (value: string): string =>
  value
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&");

export const normalizeHtmlContent = (value: unknown): string => {
  if (typeof value !== "string") return "";

  let normalized = value.trim();
  if (!normalized) return "";

  // Alguns payloads podem chegar com HTML serializado/escapado (ex.: \" e \/)
  if (normalized.includes("\\") && /\\["'\/nr]/.test(normalized)) {
    normalized = normalized
      .replace(/\\r\\n/g, "\n")
      .replace(/\\n/g, "\n")
      .replace(/\\r/g, "")
      .replace(/\\"/g, '"')
      .replace(/\\'/g, "'")
      .replace(/\\\//g, "/");
  }

  if (normalized.includes("&lt;") && normalized.includes("&gt;")) {
    normalized = decodeBasicHtmlEntities(normalized);
  }

  return normalized.trim();
};

export const hasRenderableHtmlContent = (value: unknown): boolean => {
  const normalized = normalizeHtmlContent(value);
  if (!normalized) return false;

  if (/<img\b/i.test(normalized)) return true;

  const textOnly = normalized
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .trim();

  return textOnly.length > 0;
};

export const pickRenderableHtml = (...values: unknown[]): string => {
  for (const value of values) {
    if (hasRenderableHtmlContent(value)) {
      return normalizeHtmlContent(value);
    }
  }

  return "";
};
