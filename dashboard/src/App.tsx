import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { FeedView, useFeed } from "./Feed";
import { DetailView } from "./Detail";
import { Hex, Link } from "./ui";
import {
  entry,
  feedSearch,
  projectPath,
  snapshot,
  subscribe,
} from "./navigation";
import "./style.css";
export function App() {
  const route = useSyncExternalStore(subscribe, snapshot);
  const navigation = useMemo(
    () => ({ route, position: entry() }),
    [route],
  ).position;
  const path = location.pathname,
    search = location.search;
  const query = path === "/" ? feedSearch(search) : navigation.feed.search;
  const feed = useFeed(query, navigation.feed.count, navigation.id);
  const restored = useRef<string | null>(null);
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
    document.title = path === "/" ? "Hive · Newsfeed" : "Hive · Work detail";
    if (restored.current === route) return;
    if (
      path === "/" &&
      (!feed.data ||
        (feed.data.cards.length < navigation.feed.count &&
          feed.data.next_cursor))
    )
      return;
    window.scrollTo(0, navigation.scroll);
    restored.current = route;
  }, [route, path, navigation, feed.data]);
  if (incompatible)
    return (
      <main className="incompatible">
        <button onClick={() => location.reload()}>New version — reload</button>
      </main>
    );
  const project = new URLSearchParams(query).get("project");
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
          <Link
            to={projectPath("")}
            className={!project ? "project selected" : "project"}
          >
            <span>All projects</span>
          </Link>
          {feed.data?.projects.map((p) => (
            <Link
              key={p.id}
              to={projectPath(p.id)}
              className={project === p.id ? "project selected" : "project"}
            >
              <span>{p.id}</span>
            </Link>
          ))}
        </div>
        <p className="rail-note">Local observation</p>
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
          <FeedView search={query} {...feed} />
        ) : (
          <DetailView key={path} path={path} search={search} />
        )}
      </main>
    </>
  );
}
