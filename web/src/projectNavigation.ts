import { createContext, useContext, useEffect } from "react";
import { go, useQuery } from "./ui";

export const ProjectRoute = createContext({ hash: "", active: true, go });
export const useProjectRoute = () => useContext(ProjectRoute);

export function useProjectList() {
  const projects = useQuery<any[]>("list_projects");
  useEffect(() => {
    const refresh = () => { void projects.reload(); };
    addEventListener("projects-changed", refresh);
    return () => removeEventListener("projects-changed", refresh);
  }, [projects.reload]);
  return projects;
}
