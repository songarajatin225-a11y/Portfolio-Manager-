"use client";

import type * as React from "react";

/** Dashboard widgets contributed by later modules (market intelligence, portfolio summary). */
export const EXTRA_WIDGETS: { key: string; label: string; wide?: boolean; render: () => React.ReactNode }[] = [];
