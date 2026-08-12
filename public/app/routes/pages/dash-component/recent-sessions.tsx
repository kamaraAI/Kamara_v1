import { DashboardSidebar, DashboardTopbar } from ".";
import { BookOpen, Grid2x2, List } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { dashboardApi, type DashboardSession } from "../dashboard-api";

const dummySessions: DashboardSession[] = [
  {
    id: "demo-physics-101",
    subject: "Physics",
    course: "Science",
    topic: "Fundamentals of Motion",
    user_prompt: "Explain Newton's laws with examples",
    created_at: "2026-08-02T10:30:00.000Z",
    is_active: true,
  },
  {
    id: "demo-chemistry-201",
    subject: "Chemistry",
    course: "Science",
    topic: "Atomic Structure Review",
    user_prompt: "Summarize atoms, ions, and isotopes",
    created_at: "2026-08-01T15:20:00.000Z",
    is_active: false,
  },
  {
    id: "demo-math-301",
    subject: "Mathematics",
    course: "STEM",
    topic: "Quadratic Equations",
    user_prompt: "Walk me through solving quadratics step by step",
    created_at: "2026-07-31T08:10:00.000Z",
    is_active: false,
  },
];

export function meta() {
  return [
    { title: "Recent Sessions | Kamara AI" },
    {
      name: "description",
      content: "Access your recently viewed sessions.",
    },
  ];
}

export default function RecentSessionsPage() {
  const [isSidebarOpen, setSidebarOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [sessions, setSessions] = useState<DashboardSession[]>([]);
  const [error, setError] = useState("");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

  useEffect(() => {
    let ignore = false;

    dashboardApi
      .getSessions()
      .then((data) => {
        if (!ignore) {
          setSessions(data.sessions);
          setError("");
        }
      })
      .catch((loadError) => {
        if (!ignore) {
          setError(loadError instanceof Error ? loadError.message : "Could not load your recent sessions.");
        }
      });

    return () => {
      ignore = true;
    };
  }, []);

  const filteredCourses = useMemo(() => {
    const source = sessions.length > 0 ? sessions : dummySessions;

    if (!searchQuery) {
      return source;
    }
    return source.filter(
      (course) =>
        getSessionTitle(course).toLowerCase().includes(searchQuery.toLowerCase()) ||
        getSessionDescription(course).toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [searchQuery, sessions]);

  return (
    <main className="min-h-screen bg-slate-100 text-slate-900">
      <DashboardSidebar isOpen={isSidebarOpen} onClose={() => setSidebarOpen(false)} />
      <section className="md:ml-[232px] min-h-screen pt-[92px] md:pt-[92px]" aria-label="Recent Sessions">
        <DashboardTopbar onMenuClick={() => setSidebarOpen(true)} onSearchChange={setSearchQuery} />
        <div className="p-6">
          <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-3xl font-bold text-slate-900">Recent Sessions</h1>
              <p className="mt-2 text-sm text-slate-500">Switch between a grid or list view.</p>
            </div>
            <div className="inline-flex rounded-full border border-slate-200 bg-white p-1 shadow-sm">
              <button
                type="button"
                onClick={() => setViewMode("grid")}
                className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition ${
                  viewMode === "grid" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"
                }`}
                aria-pressed={viewMode === "grid"}
              >
                <Grid2x2 size={16} />
                Grid
              </button>
              <button
                type="button"
                onClick={() => setViewMode("list")}
                className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition ${
                  viewMode === "list" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"
                }`}
                aria-pressed={viewMode === "list"}
              >
                <List size={16} />
                List
              </button>
            </div>
          </div>
          {error ? (
            <div className="mb-6 rounded-3xl border border-red-200 bg-red-50 px-5 py-4 text-sm font-medium text-red-700">
              {error}
            </div>
          ) : null}
          <div className={viewMode === "grid" ? "grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3" : "space-y-4"}>
            {filteredCourses.map((course) => (
              <div
                key={course.id}
                className={`bg-white shadow-md hover:shadow-lg transition-shadow ${
                  viewMode === "grid"
                    ? "rounded-2xl p-6 flex flex-col justify-between"
                    : "rounded-3xl p-5 sm:p-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"
                }`}
              >
                <div className={viewMode === "list" ? "flex items-center gap-4" : ""}>
                  <div className="mb-4 text-blue-600 sm:mb-0">
                    <BookOpen size={24} />
                  </div>
                  <div>
                    <h2 className="text-lg font-semibold text-slate-800">{getSessionTitle(course)}</h2>
                    <p className="mt-2 text-sm text-slate-600">{getSessionDescription(course)}</p>
                    {course.created_at ? <p className="mt-3 text-xs text-slate-500">Started {formatDate(course.created_at)}</p> : null}
                  </div>
                </div>
                <a
                  href="/ongoing/learning"
                  className={`text-sm font-semibold text-blue-600 hover:text-blue-700 ${
                    viewMode === "list" ? "self-start sm:self-center" : "self-start"
                  }`}
                >
                  Continue Learning &rarr;
                </a>
              </div>
            ))}
            {filteredCourses.length === 0 && (
              <div className="col-span-full text-center py-12 text-slate-500">
                <p className="text-lg font-semibold">No sessions found</p>
                <p>Try adjusting your search query.</p>
              </div>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}

function getSessionTitle(session: DashboardSession) {
  return session.topic || session.user_prompt || session.course || session.subject || "Untitled session";
}

function getSessionDescription(session: DashboardSession) {
  const subject = session.subject || session.course || "Learning session";
  const status = session.is_active ? "Active" : "Saved";
  return `${status} ${subject} workspace.`;
}

function formatDate(date: string) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
  }).format(new Date(date));
}
