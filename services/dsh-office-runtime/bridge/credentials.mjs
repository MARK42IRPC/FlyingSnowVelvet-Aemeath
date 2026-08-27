import { CredentialProvider } from "@deepseek-ai/dsh-credentials";

const values = new Map();

export function setRuntimeCredential(ref, value) {
  const name = String(ref ?? "").trim();
  const secret = String(value ?? "").trim();
  if (!name) throw new TypeError("credential reference cannot be empty");
  if (secret) values.set(name, secret);
  else values.delete(name);
}

export class MemoryCredentialProvider extends CredentialProvider {
  constructor(ctx) {
    super(ctx);
  }

  async resolve(ref) {
    const value = values.get(String(ref));
    return value ? { value, source: "fsv-memory" } : undefined;
  }

  async describe(ref) {
    return {
      configured: values.has(String(ref)),
      source: values.has(String(ref)) ? "fsv-memory" : undefined,
      writable: false,
    };
  }

  async set() {
    throw new Error("office runtime credentials are supplied by the desktop process");
  }

  async unset() {
    throw new Error("office runtime credentials are supplied by the desktop process");
  }
}

export default MemoryCredentialProvider;
