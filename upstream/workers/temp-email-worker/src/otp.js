export function safeDecodeBase64(value) {
  try {
    const compact = String(value || "").replace(/\s+/g, "");
    if (!compact || compact.length % 4 === 1) return "";
    const binary = atob(compact);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch {
    return "";
  }
}

export function decodeQuotedPrintable(value) {
  try {
    const source = String(value || "").replace(/=\r?\n/g, "");
    const bytes = [];
    for (let index = 0; index < source.length; index += 1) {
      if (source[index] === "=" && /^[0-9A-F]{2}$/i.test(source.slice(index + 1, index + 3))) {
        bytes.push(Number.parseInt(source.slice(index + 1, index + 3), 16));
        index += 2;
      } else {
        bytes.push(source.charCodeAt(index) & 0xff);
      }
    }
    return new TextDecoder().decode(Uint8Array.from(bytes));
  } catch {
    return String(value || "");
  }
}

export function decodeMimeWords(value) {
  return String(value || "").replace(/=\?([^?]+)\?([bq])\?([^?]+)\?=/gi, (_match, _charset, mode, data) => {
    return mode.toLowerCase() === "b"
      ? safeDecodeBase64(data)
      : decodeQuotedPrintable(String(data).replace(/_/g, " "));
  });
}

export function decodedMimeBodies(raw) {
  const values = [String(raw || "")];
  const base64Pattern = /content-transfer-encoding:\s*base64[\s\S]*?\r?\n\r?\n([a-z0-9+/=\r\n]{16,})/gi;
  const quotedPattern = /content-transfer-encoding:\s*quoted-printable[\s\S]*?\r?\n\r?\n([\s\S]*?)(?=\r?\n--[^\r\n]+|$)/gi;
  for (const match of String(raw || "").matchAll(base64Pattern)) {
    const decoded = safeDecodeBase64(match[1]);
    if (decoded) values.push(decoded);
  }
  for (const match of String(raw || "").matchAll(quotedPattern)) {
    const decoded = decodeQuotedPrintable(match[1]);
    if (decoded) values.push(decoded);
  }
  return values;
}

export function extractOTP(...sources) {
  const text = sources.filter(Boolean).join("\n");
  const contextual = [
    /(?:confirmation|verification|security|login|one[- ]time|otp|验证码|確認碼|認證碼)[^A-Z0-9]{0,40}([A-Z0-9]{3}-[A-Z0-9]{3})/i,
    /(?:confirmation|verification|security|login|one[- ]time|otp|验证码|確認碼|認證碼)[^0-9]{0,40}(\d{6})/i,
  ];
  for (const pattern of contextual) {
    const match = text.match(pattern);
    if (match) return match[1].toUpperCase();
  }
  const dashed = text.match(/\b([A-Z0-9]{3}-[A-Z0-9]{3})\b/i);
  if (dashed) return dashed[1].toUpperCase();
  const digits = text.match(/(?:^|\D)(\d{6})(?:\D|$)/);
  return digits ? digits[1] : "";
}
