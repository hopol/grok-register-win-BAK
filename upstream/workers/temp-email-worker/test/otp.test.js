import test from "node:test";
import assert from "node:assert/strict";

import { decodeMimeWords, decodedMimeBodies, extractOTP } from "../src/otp.js";

test("extracts xAI dashed confirmation codes", () => {
  assert.equal(extractOTP("Your xAI confirmation code", "Use code 9GM-JJI"), "9GM-JJI");
});

test("extracts six digit verification codes", () => {
  assert.equal(extractOTP("验证码", "您的验证码是 302959"), "302959");
});

test("decodes encoded MIME subjects", () => {
  assert.equal(decodeMimeWords("=?UTF-8?B?WW91ciB4QUkgY29kZQ==?="), "Your xAI code");
});

test("decodes base64 MIME bodies before OTP matching", () => {
  const raw = "Content-Transfer-Encoding: base64\r\n\r\nVXNlIGNvZGUgQUJDLURFRg==\r\n";
  assert.equal(extractOTP(...decodedMimeBodies(raw)), "ABC-DEF");
});
