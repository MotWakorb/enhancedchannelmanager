import type { FFMPEGBuilderState } from '../../types/ffmpegBuilder';

// ---------------------------------------------------------------------------
// Stream Options state for IPTV smart defaults
// ---------------------------------------------------------------------------

export interface StreamOptionsState {
    networkResilience: boolean;
    reconnectDelayMax: string;
    streamAnalysis: boolean;
    analyzeduration: string;
    probesize: string;
    errorHandling: boolean;
    bufferSize: string;
    streamMapping: boolean;
}

export const DEFAULT_STREAM_OPTIONS: StreamOptionsState = {
    networkResilience: true,
    reconnectDelayMax: '10',
    streamAnalysis: true,
    analyzeduration: '5000000',
    probesize: '5000000',
    errorHandling: true,
    bufferSize: '512',
    streamMapping: true,
};

/** Convert StreamOptionsState into FFMPEGBuilderState fragments */
export function buildIPTVOptions(opts: StreamOptionsState): {
    inputOptions: Record<string, string>;
    globalOptions: Record<string, string>;
    streamMappings: FFMPEGBuilderState['streamMappings'];
} {
    const inputOptions: Record<string, string> = {};
    const globalOptions: Record<string, string> = {};

    if (opts.errorHandling) {
        globalOptions['fflags'] = '+genpts+discardcorrupt';
        globalOptions['err_detect'] = 'ignore_err';
    }

    if (opts.networkResilience) {
        inputOptions['reconnect'] = '1';
        inputOptions['reconnect_streamed'] = '1';
        inputOptions['reconnect_delay_max'] = opts.reconnectDelayMax;
    }

    if (opts.streamAnalysis) {
        inputOptions['analyzeduration'] = opts.analyzeduration;
        inputOptions['probesize'] = opts.probesize;
    }

    inputOptions['thread_queue_size'] = opts.bufferSize;

    const streamMappings: FFMPEGBuilderState['streamMappings'] = opts.streamMapping
        ? [
            { inputIndex: 0, streamType: 'video', streamIndex: 0, outputIndex: 0, label: 'First video' },
            { inputIndex: 0, streamType: 'audio', streamIndex: 0, outputIndex: 1, label: 'First audio' },
        ]
        : [];

    return { inputOptions, globalOptions, streamMappings };
}