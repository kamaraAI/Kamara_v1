import { getBaseUrl } from "./api-config";
import { getAuthHeaders } from "./auth/session";

export type SubscriptionFeatureRequest = {
  feature: string;
  quantity?: number;
  sizeBytes?: number;
  contentChars?: number;
  hasExternalSource?: boolean;
};

export type SubscriptionAccessResponse = {
  allowed: boolean;
  error_code?: string | null;
  reason?: string | null;
  required_plan?: string | null;
  feature?: string | null;
  plan_tier?: string | null;
  subscription_status?: string | null;
  message?: string | null;
  used?: number | null;
  limit?: number | null;
  remaining?: number | null;
  limit_mb?: number | null;
  limit_chars?: number | null;
};

export class SubscriptionRequiredError extends Error {
  code = "subscription_required";
  reason?: string;
  requiredPlan?: string;
  feature?: string;
  upgradeUrl?: string;
  limit?: number | null;
  remaining?: number | null;

  constructor(message: string, payload: Partial<SubscriptionAccessResponse> & { upgradeUrl?: string } = {}) {
    super(message);
    this.name = "SubscriptionRequiredError";
    this.reason = payload.reason ?? undefined;
    this.requiredPlan = payload.required_plan ?? undefined;
    this.feature = payload.feature ?? undefined;
    this.upgradeUrl = payload.upgradeUrl ?? undefined;
    this.limit = payload.limit ?? null;
    this.remaining = payload.remaining ?? null;
  }
}

async function apiFetch(path: string, init: RequestInit) {
  try {
    return await fetch(`${getBaseUrl()}${path}`, init);
  } catch {
    throw new Error("Could not reach the backend API. Please check your internet connection or try again later.");
  }
}

function buildSubscriptionError(payload: Partial<SubscriptionAccessResponse> & { upgrade_url?: string }) {
  const message = payload.message || "Upgrade required.";
  return new SubscriptionRequiredError(message, {
    ...payload,
    upgradeUrl: payload.upgrade_url,
  });
}

export async function checkSubscriptionFeature(payload: SubscriptionFeatureRequest): Promise<SubscriptionAccessResponse> {
  const response = await apiFetch("/subscription/check", {
    method: "POST",
    headers: {
      ...getAuthHeaders(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw buildSubscriptionError(await response.json().catch(() => ({})));
  }

  return response.json();
}

export async function consumeSubscriptionFeature(payload: SubscriptionFeatureRequest): Promise<SubscriptionAccessResponse> {
  const response = await apiFetch("/subscription/consume", {
    method: "POST",
    headers: {
      ...getAuthHeaders(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw buildSubscriptionError(await response.json().catch(() => ({})));
  }

  return response.json();
}

export function isSubscriptionRequiredError(error: unknown): error is SubscriptionRequiredError {
  return error instanceof SubscriptionRequiredError || (typeof error === "object" && error !== null && "code" in error && (error as { code?: string }).code === "subscription_required");
}
