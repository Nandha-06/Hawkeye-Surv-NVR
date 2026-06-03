import { browser } from '$app/environment';

class DevModeState {
    #enabled = $state(false);

    constructor() {
        if (browser) {
            const stored = localStorage.getItem('dc_dev_mode');
            this.#enabled = stored === 'true';
        }
    }

    get enabled() {
        return this.#enabled;
    }

    set enabled(val: boolean) {
        this.#enabled = val;
        if (browser) {
            localStorage.setItem('dc_dev_mode', String(val));
        }
    }
}

export const devMode = new DevModeState();
