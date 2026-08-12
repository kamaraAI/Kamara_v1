import type { Route } from "./+types/dashboard";
import { useEffect, useState } from "react";
import {
  DashboardSidebar,
  DashboardTopbar,
  DashboardMetrics,
  ProgressInsights,
  WelcomePanel,
} from "./dash-component";
import { dashboardApi, type DashboardSummary } from "./dashboard-api";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Dashboard | Kamara AI" },
    {
      name: "description",
      content: "Kamara AI student learning dashboard.",
    },
  ];
}

export default function DashboardPage() {
  return (
    
      <DashboardContent />
    
  );
}

function DashboardContent() {
  const [isSidebarOpen, setSidebarOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [dashboard, setDashboard] = useState<DashboardSummary | null>(null);
  const [dashboardError, setDashboardError] = useState("");

  useEffect(() => {
    let ignore = false;

    dashboardApi
      .getSummary()
      .then((data) => {
        if (!ignore) {
          setDashboard(data);
          setDashboardError("");
        }
      })
      .catch((error) => {
        if (!ignore) {
          setDashboardError(error instanceof Error ? error.message : "Could not load your dashboard.");
        }
      });

    return () => {
      ignore = true;
    };
  }, []);

  return (
    <main className="min-h-screen bg-slate-100 text-slate-900">
      <DashboardSidebar
        isOpen={isSidebarOpen}
        onClose={() => setSidebarOpen(false)}
        userName={dashboard?.full_name}
        planTier={dashboard?.plan_tier}
      />

      <section className="md:ml-[232px] min-h-screen pt-[92px] md:pt-[92px]" aria-label="Student dashboard">
        <DashboardTopbar
          onMenuClick={() => setSidebarOpen(true)}
          onSearchChange={setSearchQuery}
          userName={dashboard?.full_name}
          planTier={dashboard?.plan_tier}
          showSearch={false}
          title="Dashboard"
        />

        <div className="max-w-[1600px] mx-auto px-4 py-4 lg:px-6 lg:py-6">
          {dashboardError ? (
            <div className="mb-6 rounded-3xl border border-red-200 bg-red-50 px-5 py-4 text-sm font-medium text-red-700">
              {dashboardError}
            </div>
          ) : null}

          <div className="space-y-6">
            <WelcomePanel fullName={dashboard?.full_name} planTier={dashboard?.plan_tier} recommendations={dashboard?.recommended_topics} />

            <div className="flex flex-col gap-6 xl:flex-row xl:items-stretch">
              <div className="xl:w-[38%] xl:self-stretch">
                <DashboardMetrics dashboard={dashboard} />
              </div>
              <div className="xl:w-[62%] xl:self-stretch">
                <ProgressInsights dashboard={dashboard} />
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
