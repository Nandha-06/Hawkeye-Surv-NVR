/**
 * Shared client-side types for the Hawkeye frontend.
 *
 * These mirror the wire shape returned by the Rust Axum backend on
 * 127.0.0.1:8080 (see src-tauri/src/server.rs). Keep this file in sync with
 * the Rust serde structs and the WS message protocol.
 */

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export type SkillParameterType =
	| 'string'
	| 'password'
	| 'number'
	| 'boolean'
	| 'select'
	| 'camera_select'
	| 'url';

export interface SkillParameter {
	name: string;
	label: string;
	type: SkillParameterType;
	default?: unknown;
	description?: string;
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	options?: any[];
	min?: number;
	max?: number;
	placeholder?: string;
	group?: string;
	platform?: 'macos' | 'linux' | 'windows';
}

export interface SkillMetadata {
	id: string;
	name: string;
	description: string;
	isDeploying?: boolean;
	version: string;
	category: string;
	path: string;
	tags: string[];
	platforms: string[];
	isInstalled: boolean;
	configParams: SkillParameter[];
	isRunning?: boolean;
	status?: 'starting' | 'ready' | 'stopped' | 'error';
}

// ---------------------------------------------------------------------------
// Model providers (LLM / VLM)
// ---------------------------------------------------------------------------

export type ApiFormat = 'openai' | 'anthropic' | 'gemini';

export interface ProviderConfig {
	enabled: boolean;
	apiKey: string;
	baseUrl: string;
	defaultModel: string;
	availableModels: string[];
	label: string;
	icon: string;
	apiFormat: ApiFormat;
}

export interface InferenceTarget {
	type: 'local-engine' | 'cloud-provider';
	engineId: string;
	modelId: string | null;
	port: number;
	provider: string | null;
	cloudModelId: string | null;
}

export interface ActiveInferenceConfig {
	llm: InferenceTarget;
	vlm: InferenceTarget;
}

// ---------------------------------------------------------------------------
// Cameras / recordings / events
// ---------------------------------------------------------------------------

export interface CameraConfig {
	id: string;
	name: string;
	source: 'rtsp' | 'webcam' | 'file';
	url?: string;
	enabled: boolean;
	masks?: PolygonZone[];
	zones?: PolygonZone[];
}

export interface PolygonZone {
	id: string;
	label?: string;
	points: Array<[number, number]>;
}

export interface CameraEvent {
	id: number;
	camera_id: string;
	label: string;
	confidence: number;
	timestamp: string;
	snapshot_path: string | null;
	severity: string | null;
}

export interface RecordingSegment {
	id: number;
	camera_id: string;
	start_time: string;
	end_time: string;
	filepath: string;
	type: 'continuous' | 'event';
}
