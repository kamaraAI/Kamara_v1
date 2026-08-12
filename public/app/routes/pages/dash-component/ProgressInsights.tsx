import { useEffect, useMemo, useState } from "react";
import type { DashboardSummary } from "../dashboard-api";
import { getSessionUserKey } from "../../auth/session";

interface ProgressInsightsProps {
  dashboard?: DashboardSummary | null;
}

type StreakState = {
  streak: number;
  lastLoginDate: string | null;
};

const streakStoragePrefix = "kamara_streak_";
const loginHistoryPrefix = "kamara_login_history_";

function getTodayKey() {
  return new Date().toISOString().slice(0, 10);
}

function readJson<T>(key: string, fallback: T) {
  if (typeof window === "undefined") {
    return fallback;
  }

  const value = localStorage.getItem(key);
  if (!value) {
    return fallback;
  }

  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  if (typeof window === "undefined") {
    return;
  }

  localStorage.setItem(key, JSON.stringify(value));
}

function getStreakKey() {
  return `${streakStoragePrefix}${getSessionUserKey()}`;
}

function getLoginHistoryKey() {
  return `${loginHistoryPrefix}${getSessionUserKey()}`;
}

function updateStreakForToday() {
  if (typeof window === "undefined") {
    return { streak: 0, lastLoginDate: null } satisfies StreakState;
  }

  const today = getTodayKey();
  const streakKey = getStreakKey();
  const historyKey = getLoginHistoryKey();
  const current = readJson<StreakState>(streakKey, { streak: 0, lastLoginDate: null });
  const history = readJson<string[]>(historyKey, []);
  const lastDate = current.lastLoginDate;

  if (lastDate === today) {
    return current;
  }

  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const yesterdayKey = yesterday.toISOString().slice(0, 10);
  const nextStreak = lastDate === yesterdayKey ? current.streak + 1 : 1;
  const updated = { streak: nextStreak, lastLoginDate: today };

  writeJson(streakKey, updated);
  writeJson(historyKey, Array.from(new Set([today, ...history])).slice(0, 14));

  return updated;
}

function buildBars(streak: number) {
  const base = [42, 68, 54, 78, 46, 60, 50];
  return base.map((value, index) => ({
    height: Math.min(100, Math.max(20, value + Math.min(streak * 3, 18) - (index % 2 === 0 ? 0 : 6))),
  }));
}

export function ProgressInsights({ dashboard }: ProgressInsightsProps) {
  const [streak, setStreak] = useState<StreakState>({ streak: 0, lastLoginDate: null });

  useEffect(() => {
    setStreak(updateStreakForToday());
  }, []);

  const bars = useMemo(() => buildBars(streak.streak), [streak.streak]);
  const completed = dashboard?.recent_activity.filter((item) => item.type === "exam").length ?? 0;
  const pending = dashboard?.recommended_topics.length ?? 0;
  const progressPercent = Math.min(100, Math.round((completed / Math.max(completed + pending, 1)) * 100));

  return (
    <section className="flex h-full flex-col gap-6 rounded-4xl border border-slate-200 bg-white p-5 shadow-sm lg:p-6" aria-label="Progress insights">
      <article className="flex flex-1 flex-col rounded-4xl border border-slate-200 bg-white p-5 shadow-sm lg:p-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900 lg:text-lg">Project Progress</h2>
            <p className="text-sm text-slate-500">Your current learning momentum.</p>
          </div>
          <div className="rounded-full bg-blue-50 px-3 py-1.5 text-sm font-semibold text-blue-700">
            {progressPercent}% completed
          </div>
        </div>

        <div className="mt-6 grid flex-1 gap-6 lg:grid-cols-[1.05fr_0.95fr]">
          <div className="rounded-[1.75rem] bg-slate-50 p-4 lg:p-5">
            <div className="mx-auto flex max-w-[280px] flex-col items-center">
              <div
                className="relative flex h-40 w-40 items-center justify-center rounded-full lg:h-48 lg:w-48"
                style={{
                  background: `conic-gradient(#2563eb 0deg ${progressPercent * 3.6}deg, #dbeafe ${progressPercent * 3.6}deg 360deg)`,
                }}
              >
                <div className="flex h-28 w-28 items-center justify-center rounded-full bg-white text-center shadow-sm lg:h-36 lg:w-36">
                  <div>
                    <p className="text-3xl font-semibold text-slate-900 lg:text-4xl">{progressPercent}%</p>
                    <p className="text-sm text-slate-500">Project Ended</p>
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-center justify-center gap-5 text-sm text-slate-500">
              <span className="inline-flex items-center gap-2">
                <span className="h-3 w-3 rounded-full bg-blue-700" />
                Completed
              </span>
              <span className="inline-flex items-center gap-2">
                <span className="h-3 w-3 rounded-full bg-blue-300" />
                In Progress
              </span>
              <span className="inline-flex items-center gap-2">
                <span className="h-3 w-3 rounded-full bg-slate-300" />
                Pending
              </span>
            </div>
          </div>

          <article className="rounded-[1.75rem] border border-slate-200 bg-white p-4 shadow-sm lg:p-5">
            <h3 className="text-base font-semibold text-slate-900 lg:text-lg">Streak System</h3>
            <p className="mt-1 text-sm text-slate-500">Updates automatically when you log in on a new day.</p>
            <div className="mt-4 grid grid-cols-7 gap-2">
              {bars.map((bar, index) => (
                <div key={index} className="flex h-28 items-end justify-center rounded-2xl bg-slate-50 p-2">
                  <div className="w-full rounded-full bg-blue-600" style={{ height: `${bar.height}%` }} />
                </div>
              ))}
            </div>
            <p className="mt-4 text-sm text-slate-500">
              Current streak: <span className="font-semibold text-slate-900">{streak.streak} days</span>
            </p>
          </article>
        </div>
      </article>
    </section>
  );
}
