import { useEffect, useState, useSyncExternalStore } from "react";
import { FeedView, useFeed } from "./Feed";
import { DetailView } from "./Detail";
import { Hex, Link } from "./ui";
import "./style.css";
function subscribe(callback: () => void) {
  window.addEventListener("popstate", callback);
  return () => window.removeEventListener("popstate", callback);
}
export function App() {
  const address = useSyncExternalStore(
    subscribe,
    () => location.pathname + location.search,
  );
  const path = address.split("?")[0] ?? "/";
  const search = address.includes("?")
    ? "?" + address.split("?").slice(1).join("?")
    : "";
  const feed = useFeed(
    path === "/" ? search : (sessionStorage.getItem("hive-feed-search") ?? ""),
  );
  const [updated, setUpdated] = useState(false),
    [incompatible, setIncompatible] = useState(false);
  useEffect(() => {
    const update = () => setUpdated(true),
      schema = () => setIncompatible(true);
    window.addEventListener("hive:new-version", update);
    window.addEventListener("hive:new-schema", schema);
    return () => {
      window.removeEventListener("hive:new-version", update);
      window.removeEventListener("hive:new-schema", schema);
    };
  }, []);
  useEffect(() => {
    if (path === "/") {
      sessionStorage.setItem("hive-feed-url", address);
      sessionStorage.setItem("hive-feed-search", search);
    }
    window.scrollTo(
      0,
      Number(sessionStorage.getItem("hive-scroll:" + address) ?? 0),
    );
    document.title = path === "/" ? "Hive · Newsfeed" : "Hive · Work detail";
  }, [address, path, search]);
  if (incompatible)
    return (
      <main className="incompatible">
        <button onClick={() => location.reload()}>New version — reload</button>
      </main>
    );
  const project = new URLSearchParams(search).get("project");
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <aside className="rail">
        <Link to="/" className="brand">
          <Hex brand />
          Hive
        </Link>
        <nav aria-label="Main">
          <Link
            to="/"
            className={path === "/" ? "nav-item selected" : "nav-item"}
          >
            <span aria-hidden="true">▦</span> Newsfeed
          </Link>
        </nav>
        <div className="project-list">
          <h2 className="eyebrow">Projects</h2>
          <Link to="/" className={!project ? "project selected" : "project"}>
            <span>All projects</span>
            <span>
              {feed.data?.projects.reduce((sum, p) => sum + p.count, 0) ?? "—"}
            </span>
          </Link>
          {feed.data?.projects.map((p) => (
            <Link
              key={p.id}
              to={"/?project=" + encodeURIComponent(p.id)}
              className={project === p.id ? "project selected" : "project"}
            >
              <span>
                <Hex project={p.id} />
                {p.id}
              </span>
              <span>{p.count}</span>
            </Link>
          ))}
        </div>
        <p className="rail-note">
          Read-only observations
          <br />
          Local to this Mac
        </p>
      </aside>
      <main id="main">
        {updated && (
          <div className="version-banner">
            <button onClick={() => location.reload()}>
              New version — reload
            </button>
          </div>
        )}
        {path === "/" ? (
          <FeedView search={search} {...feed} />
        ) : (
          <DetailView key={path} path={path} search={search} />
        )}
      </main>
    </>
  );
}
