import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiFetch, ApiError } from "../../api/client";
import { AppHeader } from "../shared/AppHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Course {
  id: string;
  owner_id: string;
  code: string;
  title: string;
  term: string;
  created_at: string;
}

/**
 * `/courses/new` (professor/admin). Mirrors
 * DashboardPage's submit-with-`submitting`-flag pattern - no page-level
 * loader is needed since the form renders immediately with no async data
 * dependency. A non-professor who reaches this route and submits gets a
 * 403 from POST /courses (`require_role("professor", "admin")`);
 * that is surfaced as a normal form error, not a route-level redirect
 * or a crash.
 */
export function CourseCreatePage() {
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [term, setTerm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const response = await apiFetch("/courses", {
        method: "POST",
        body: JSON.stringify({ code, title, term }),
      });
      const course = (await response.json()) as Course;
      navigate(`/courses/${course.id}`, { replace: true });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      setErrorMessage(error instanceof Error ? error.message : "Action failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main>
      <AppHeader />
      <div className="mx-auto max-w-[480px] px-8 py-8">
        <h1 className="mb-8 text-[28px] font-semibold leading-[1.2] text-slate-900">
          Create a course
        </h1>

        {errorMessage && (
          <Alert variant="destructive" className="mb-4">
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        )}

        <Card className="p-6">
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => void handleSubmit(event)}
          >
            <div className="flex flex-col gap-2">
              <Label htmlFor="course-code">Course code</Label>
              <Input
                id="course-code"
                placeholder="CMPT 120"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="course-title">Title</Label>
              <Input
                id="course-title"
                placeholder="Introduction to Computing Science"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="course-term">Term</Label>
              <Input
                id="course-term"
                placeholder="Fall 2026"
                value={term}
                onChange={(event) => setTerm(event.target.value)}
                required
              />
            </div>
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Creating..." : "Create course"}
            </Button>
          </form>
        </Card>
      </div>
    </main>
  );
}
