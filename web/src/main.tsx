import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { DisplayProvider } from "./DisplayPreferences";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><DisplayProvider><App /></DisplayProvider></StrictMode>);
