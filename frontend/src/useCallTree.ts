import { useCallback, useMemo, useRef, useState } from "react";
import { api, CallNode, GroupNode, MoreNode, TreeItem } from "./api";

/** Keys: "root"; call "c<id>"; group "g<parent|r>:<code>"; paging stub "m<groupKey>@<offset>". */
export type Key = string;
export const callKey = (id: number): Key => `c${id}`;
const groupKey = (g: { parent_id: number | null; code_id: number }): Key => `g${g.parent_id ?? "r"}:${g.code_id}`;
const PAGE = 50;

export type Item = TreeItem | (MoreNode & { offset: number; group: Key }) | HiddenStub;

/** Siblings removed by the viewer's filters, shown as one node so the structure stays honest (FR-VIZ-6). */
export interface HiddenStub {
  type: "hidden";
  count: number;
}

export interface Visible {
  key: Key;
  parent: Key | null;
  item: Item;
}

function keyOf(item: TreeItem): Key {
  if (item.type === "call") return callKey(item.id);
  if (item.type === "group") return groupKey(item);
  return `m${item.parent_id ?? "r"}`;
}

/** Lazily loaded, expandable call tree of one run. Mutable maps plus a version counter keep updates cheap. */
export function useCallTree(run: string | null) {
  const items = useRef(new Map<Key, Item>());
  const kids = useRef(new Map<Key, Key[]>());
  const expanded = useRef(new Set<Key>());
  const loading = useRef(new Map<Key, Promise<void>>());
  const [version, setVersion] = useState(0);
  const bump = () => setVersion((v) => v + 1);

  const reset = useCallback(() => {
    items.current = new Map();
    kids.current = new Map();
    expanded.current = new Set();
    loading.current = new Map();
    bump();
  }, []);

  const register = (list: Item[]): Key[] =>
    list.map((it) => {
      const key = it.type === "more" && "group" in it ? `m${it.group}@${it.offset}` : keyOf(it as TreeItem);
      items.current.set(key, it);
      return key;
    });

  const loadGroupPage = async (gkey: Key, offset: number) => {
    const g = items.current.get(gkey) as GroupNode;
    const members = await api.group(run!, g.parent_id, g.code_id, offset, PAGE);
    const page: Item[] = [...members];
    if (offset + PAGE < g.count) {
      page.push({ type: "more", parent_id: g.parent_id, remaining: g.count - offset - PAGE, offset: offset + PAGE, group: gkey });
    }
    const existing = (kids.current.get(gkey) ?? []).filter((k) => !k.startsWith(`m${gkey}@`));
    const fresh = register(page).filter((k) => !existing.includes(k));
    kids.current.set(gkey, [...existing, ...fresh]);
  };

  const ensureChildren = useCallback(
    (key: Key): Promise<void> => {
      if (!run || kids.current.has(key)) return Promise.resolve();
      let p = loading.current.get(key);
      if (!p) {
        p = (async () => {
          if (key.startsWith("g")) return loadGroupPage(key, 0);
          const list = await api.tree(run, key === "root" ? null : Number(key.slice(1)));
          kids.current.set(key, register(list));
        })();
        loading.current.set(key, p);
      }
      return p;
    },
    [run],
  );

  const toggle = useCallback(
    async (key: Key) => {
      if (expanded.current.has(key)) {
        expanded.current.delete(key);
        bump();
        return;
      }
      await ensureChildren(key);
      expanded.current.add(key);
      bump();
    },
    [ensureChildren],
  );

  const loadMore = useCallback(
    async (key: Key) => {
      const more = items.current.get(key) as MoreNode & { offset: number; group: Key };
      if (!more || !("group" in more)) return;
      await loadGroupPage(more.group, more.offset);
      bump();
    },
    [run],
  );

  /** Expand everything between the roots and a call, so it is on screen. Returns its key. */
  const reveal = useCallback(
    async (callId: number): Promise<Key | null> => {
      if (!run) return null;
      const path = await api.path(run, callId);
      await ensureChildren("root");
      let parent: Key = "root";
      for (const node of path) {
        const key = callKey(node.id);
        await ensureChildren(parent);
        let siblings = kids.current.get(parent) ?? [];
        if (!siblings.includes(key)) {
          // it is folded into a "×N" group, or beyond the page: show it inside the group (or pin it to the parent)
          const g = siblings.find((k) => k.startsWith("g") && (items.current.get(k) as GroupNode).code_id === node.code_id);
          if (g) {
            await ensureChildren(g);
            expanded.current.add(g);
            const members = kids.current.get(g)!;
            if (!members.includes(key)) {
              items.current.set(key, node);
              const more = members.findIndex((k) => k.startsWith("m"));
              members.splice(more < 0 ? members.length : more, 0, key);
            }
          } else {
            items.current.set(key, node);
            siblings = [...siblings, key];
            kids.current.set(parent, siblings);
          }
        }
        if (node.id !== callId) {
          await ensureChildren(key);
          expanded.current.add(key);
        }
        parent = key;
      }
      bump();
      return callKey(callId);
    },
    [run, ensureChildren],
  );

  const visible = useMemo(() => {
    const out: Visible[] = [];
    const walk = (parent: Key, depth: number) => {
      for (const key of kids.current.get(parent) ?? []) {
        const item = items.current.get(key);
        if (!item) continue;
        out.push({ key, parent: parent === "root" ? null : parent, item });
        if (expanded.current.has(key) && depth < 200) walk(key, depth + 1);
      }
    };
    walk("root", 0);
    return out;
  }, [version]);

  const isExpanded = useCallback((key: Key) => expanded.current.has(key), [version]);
  const callItem = useCallback((key: Key) => items.current.get(key) as CallNode | undefined, [version]);

  return { visible, toggle, loadMore, reveal, reset, ensureChildren, isExpanded, callItem, version, bump };
}
