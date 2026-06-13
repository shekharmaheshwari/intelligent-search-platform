import { writable, get, type Writable } from 'svelte/store';

/**
 * Abstract base for all application stores.
 *
 * Wraps a Svelte ``writable`` with protected mutation helpers so subclasses
 * can expose strongly-typed action methods without leaking the raw writable.
 *
 * @template T  Shape of the state slice this store manages.
 */
export abstract class StoreBase<T> {
  private _store: Writable<T>;

  /** Svelte-compatible subscribe function; bind in templates with ``$storeName``. */
  readonly subscribe: Writable<T>['subscribe'];

  constructor(initial: T) {
    this._store = writable(initial);
    this.subscribe = this._store.subscribe;
  }

  /** Replace the entire state with ``value``. Always creates a new reference so derived stores re-run. */
  protected set(value: T): void {
    this._store.set(value);
  }

  /** Derive the next state from the current one. ``fn`` must return a new object — never mutate in place. */
  protected update(fn: (value: T) => T): void {
    this._store.update(fn);
  }

  /** Read the current state synchronously (outside reactive contexts). */
  getValue(): T {
    return get(this._store);
  }
}
