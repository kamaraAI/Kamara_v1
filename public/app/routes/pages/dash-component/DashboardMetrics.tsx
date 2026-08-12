import { Award, BookOpenCheck, Clock3, ListTodo } from "lucide-react";
import type { DashboardSummary } from "../dashboard-api";

interface DashboardMetricsProps {
  dashboard?: DashboardSummary | null;
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

export function DashboardMetrics({ dashboard }: DashboardMetricsProps) {
  const recentActivityCount = dashboard?.recent_activity.length ?? 0;
  const completedCount = dashboard?.recent_activity.filter((item) => item.type === "exam").length ?? 0;
  const pendingCount = dashboard?.recommended_topics.length ?? 0;
  const enrolledCount = Math.max(recentActivityCount, pendingCount);

  const metrics = [
    {
      label: "Enrolled Classes",
      value: formatNumber(enrolledCount),
      note: "Derived from your dashboard activity",
      icon: <BookOpenCheck size={20} />,
      tone: "bg-blue-600 text-white",
    },
    {
      label: "Completed",
      value: formatNumber(completedCount),
      note: "Finished learning tasks and quizzes",
      icon: <Award size={20} />,
      tone: "bg-slate-950 text-white",
    },
    {
      label: "Pending",
      value: formatNumber(pendingCount),
      note: "Recommended topics waiting for you",
      icon: <Clock3 size={20} />,
      tone: "bg-white text-slate-900",
    },
    {
      label: "Recent Activity",
      value: formatNumber(recentActivityCount),
      note: "Latest actions from your dashboard",
      icon: <ListTodo size={20} />,
      tone: "bg-white text-slate-900",
    },
  ];

  return (
    <section className="flex h-full flex-col rounded-4xl border border-slate-200 bg-white p-5 shadow-sm lg:p-6" aria-label="Dashboard metrics">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-slate-900 lg:text-lg">Stats</h2>
          <p className="text-sm text-slate-500">A quick look at your learning activity.</p>
        </div>
      </div>

      <div className="mt-6 grid flex-1 gap-4 sm:grid-cols-2 xl:grid-cols-2">
        {metrics.map((metric) => (
          <article key={metric.label} className={`rounded-[1.75rem] border border-slate-200 p-4 shadow-sm lg:p-5 ${metric.tone}`}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-semibold opacity-80">{metric.label}</p>
                <p className="mt-3 text-2xl font-semibold leading-none lg:text-3xl">{metric.value}</p>
              </div>
              <span className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-white/15">
                {metric.icon}
              </span>
            </div>
            <p className="mt-4 text-sm opacity-80">{metric.note}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
