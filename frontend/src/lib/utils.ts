import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

// Shadcn Svelte / Bits UI utility types
export type WithElementRef<T, HTMLAttribute = any> = T & {
    ref?: HTMLAttribute | null;
};

export type WithoutChildren<T> = Omit<T, "children">;
export type WithoutChildrenOrChild<T> = Omit<T, "children" | "child">;
