import { DashboardSidebar, DashboardTopbar } from "./dash-component";
import { BookOpen, BrainCircuit, FlaskConical, Atom, Grid3X3, Calculator, Plus, Grid2x2, List } from "lucide-react";
import { useState, useMemo, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { getGeneratedCourseStorageKey, submitCoursePrompt } from "./genie-api";
import CourseModal from "./ongoing/courseModal";
import { GenieChatPanel } from "./dash-component";

type Course = {
  title: string;
  icon: ReactNode;
  description: string;
};

const courses = [
  { title: "Mathematics", icon: <BookOpen size={24} />, description: "Learn the fundamentals of Mathematics." },
  { title: "Physics", icon: <Atom size={24} />, description: "Explore the strange world of Physics." },
  { title: "Chemistry", icon: <FlaskConical size={24} />, description: "Understand the structure, properties, and reactions of organic compounds." },
  { title: "Linear Algebra", icon: <Grid3X3 size={24} />, description: "Master vectors, matrices, and linear transformations." },
  { title: "Calculus II", icon: <Calculator size={24} />, description: "Continue your journey into the world of calculus." },
  { title: "AI for Beginners", icon: <BrainCircuit size={24} />, description: "An introduction to the concepts of Artificial Intelligence." },
] satisfies Course[];
export function meta() {
  return [
    { title: "Genie | Kamara AI" },
    {
      name: "description",
      content: "create and manage your agents.",
    },
  ];
}

export default function CoursesPage() {
  const navigate = useNavigate();
  const [isSidebarOpen, setSidebarOpen] = useState(false);
  const [isGenieOpen, setIsGenieOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCourse, setSelectedCourse] = useState<Course | null>(null);
  const [submitError, setSubmitError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

  const filteredCourses = useMemo(() => {
    if (!searchQuery) {
      return courses;
    }
    return courses.filter(
      (course) => course.title.toLowerCase().includes(searchQuery.toLowerCase()) || course.description.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [searchQuery]);

  const handleCoursePromptSubmit = async ({ course, prompt, photos }: { course: Course; prompt: string; photos: File[] }) => {
    setIsSubmitting(true);
    setSubmitError("");

    try {
      const generatedCourse = await submitCoursePrompt({
        courseTitle: course.title,
        prompt,
      });

      sessionStorage.setItem(
        getGeneratedCourseStorageKey(),
        JSON.stringify({
          course,
          prompt,
          generatedCourse,
        })
      );

      setSelectedCourse(null);
      navigate("/ongoing/learning");
    } catch (error) {
      if (isSubscriptionRequiredError(error)) {
        const params = new URLSearchParams();
        if (error.feature) params.set("feature", error.feature);
        if (error.reason) params.set("reason", error.reason);
        if (error.requiredPlan) params.set("plan", error.requiredPlan);
        navigate(`/upgrade?${params.toString()}`);
        return;
      }
      setSubmitError(error instanceof Error ? error.message : "Could not send your prompt. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-100 text-slate-900">
      <DashboardSidebar isOpen={isSidebarOpen} onClose={() => setSidebarOpen(false)} />
      <section
        className={`min-h-screen pt-[92px] md:pt-[92px] transition-[margin] duration-300 ${
          isGenieOpen ? "md:ml-[232px] md:mr-[420px]" : "md:ml-[232px]"
        }`}
        aria-label="Courses"
      >
        <DashboardTopbar onMenuClick={() => setSidebarOpen(true)} onSearchChange={setSearchQuery} />
        <div className="p-6">
          <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-3xl font-bold text-slate-900">Genie</h1>
              <p className="mt-2 text-sm text-slate-500">Switch between a grid or list view.</p>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-end">
              <button
                type="button"
                onClick={() => setIsGenieOpen((current) => !current)}
                className="inline-flex items-center gap-2 rounded-full bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700"
              >
                <Plus size={16} aria-hidden="true" />
                Add Genie
              </button>
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
          </div>
          <div className={viewMode === "grid" ? "grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3" : "space-y-4"}>
            {filteredCourses.map((course) => (
              <button
                key={course.title}
                type="button"
                onClick={() => {
                  setSubmitError("");
                  setSelectedCourse(course);
                }}
                className={`bg-white text-left shadow-md transition-shadow hover:shadow-lg focus:outline-none focus:ring-4 focus:ring-blue-100 ${
                  viewMode === "grid"
                    ? "flex flex-col justify-between rounded-2xl p-6"
                    : "flex w-full flex-col gap-4 rounded-3xl p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6"
                }`}
              >
                <div className={viewMode === "list" ? "flex min-w-0 flex-1 items-start gap-4" : ""}>
                  <div className={`text-blue-600 ${viewMode === "grid" ? "mb-4" : "shrink-0"}`}>{course.icon}</div>
                  <div className="min-w-0">
                    <h2 className="text-lg font-semibold text-slate-800">{course.title}</h2>
                    <p className="mt-2 text-sm text-slate-600">{course.description}</p>
                  </div>
                </div>
                <span className={`text-sm font-semibold text-blue-600 hover:text-blue-700 ${viewMode === "list" ? "self-start sm:self-center sm:whitespace-nowrap" : "self-start"}`}>
                  Open Course &rarr;
                </span>
              </button>
            ))}
            {filteredCourses.length === 0 && (
              <div className="col-span-full text-center py-12 text-slate-500">
                <p className="text-lg font-semibold">No courses found</p>
                <p>Try adjusting your search query.</p>
              </div>
            )}
          </div>
        </div>
      </section>
      <GenieChatPanel isOpen={isGenieOpen} onClose={() => setIsGenieOpen(false)} />
      <CourseModal
        course={selectedCourse}
        errorMessage={submitError}
        isLoading={isSubmitting}
        isOpen={Boolean(selectedCourse)}
        onClose={() => {
          if (!isSubmitting) {
            setSelectedCourse(null);
          }
        }}
        onSubmit={handleCoursePromptSubmit}
      />
    </main>
  );
}
