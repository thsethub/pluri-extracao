export function sanitizeEnunciado(html: string): string {
  return html
    .replace(/<img([^>]*?)>/gi, (_match, attrs) => {
      // Remove atributos que causam overflow ou posicionamento incorreto
      const cleaned = attrs
        .replace(/\s*align\s*=\s*["'][^"']*["']/gi, "")
        .replace(/\s*width\s*=\s*["'][^"']*["']/gi, "")
        .replace(/\s*height\s*=\s*["'][^"']*["']/gi, "")
        .replace(/\s*style\s*=\s*["'][^"']*["']/gi, "");
      return `<img${cleaned} style="max-width:100%;height:auto;display:block;margin:0.75rem auto;">`;
    });
}
