// Microphone capture and playback for talking through a camera and listening to a phone.
// Audio travels as 16-bit mono PCM at 16 kHz in chunks of about 100 ms.

export const PCM_RATE = 16000
const CHUNK = PCM_RATE / 10

/** Why the microphone can't be used on this page, or null. */
export function micProblem(): string | null {
  if (!window.isSecureContext) {
    const port = location.port || (location.protocol === 'https:' ? '443' : '80')
    return `Browsers only allow the microphone on a secure page. Open the dashboard at http://localhost:${port} on this computer, or over https.`
  }
  if (!navigator.mediaDevices?.getUserMedia) return 'This browser cannot use a microphone.'
  return null
}

export function micError(err: unknown): string {
  const name = err instanceof DOMException ? err.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError')
    return 'The browser refused the microphone. Allow it for this page (the icon next to the address), then try again.'
  if (name === 'NotFoundError') return 'No microphone found on this device.'
  if (name === 'NotReadableError') return 'The microphone is in use by another app.'
  return `Could not open the microphone: ${err instanceof Error ? err.message : String(err)}`
}

/** Peak level of a chunk in dBFS, -100 for silence. */
export function peakDb(samples: ArrayLike<number>, fullScale = 1): number {
  let peak = 0
  for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i]))
  return peak > 0 ? Math.max(-100, 20 * Math.log10(peak / fullScale)) : -100
}

/** Turns audio at the context's rate into 16 kHz Int16 chunks, averaging the samples that make up each one. */
class Downsampler {
  private ratio: number
  private pos = 0
  private sum = 0
  private count = 0
  private out = new Int16Array(CHUNK)
  private len = 0
  private onChunk: (pcm: Int16Array) => void

  constructor(rate: number, onChunk: (pcm: Int16Array) => void) {
    this.ratio = rate / PCM_RATE
    this.onChunk = onChunk
  }

  push(input: Float32Array) {
    for (let i = 0; i < input.length; i++) {
      this.sum += input[i]
      this.count++
      if (++this.pos < this.ratio) continue
      this.pos -= this.ratio
      const v = this.sum / this.count
      this.sum = this.count = 0
      this.out[this.len++] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)))
      if (this.len === CHUNK) {
        this.onChunk(this.out)
        this.out = new Int16Array(CHUNK)
        this.len = 0
      }
    }
  }
}

// Hands the microphone's samples to the page. Loaded from a Blob URL, so no extra file is needed.
const TAP = `class GuardianTap extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (channel) this.port.postMessage(channel.slice(0))
    return true
  }
}
registerProcessor('guardian-tap', GuardianTap)`

/** Opens the microphone and calls onChunk with 16 kHz PCM about every 100 ms. Returns a function that stops it. */
export async function startMic(onChunk: (pcm: Int16Array) => void): Promise<() => void> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
  const ctx = new AudioContext()
  const stop = () => {
    stream.getTracks().forEach((t) => t.stop())
    ctx.close().catch(() => {})
  }
  try {
    await ctx.resume()
    const source = ctx.createMediaStreamSource(stream)
    const downsampler = new Downsampler(ctx.sampleRate, onChunk)
    let node: AudioNode | null = null
    if (ctx.audioWorklet) {
      const url = URL.createObjectURL(new Blob([TAP], { type: 'application/javascript' }))
      try {
        await ctx.audioWorklet.addModule(url)
        const tap = new AudioWorkletNode(ctx, 'guardian-tap')
        tap.port.onmessage = (e: MessageEvent<Float32Array>) => downsampler.push(e.data)
        node = tap
      } catch {
        node = null // fall back below
      } finally {
        URL.revokeObjectURL(url)
      }
    }
    if (!node) {
      const processor = ctx.createScriptProcessor(2048, 1, 1)
      processor.onaudioprocess = (e) => downsampler.push(e.inputBuffer.getChannelData(0))
      node = processor
    }
    source.connect(node)
    node.connect(ctx.destination) // keeps it running; it outputs silence
    return stop
  } catch (err) {
    stop()
    throw err
  }
}

/** Plays 16 kHz PCM chunks back to back, a little ahead of time to absorb uneven arrival. */
export class PcmPlayer {
  private ctx = new AudioContext()
  private next = 0

  resume() {
    return this.ctx.resume()
  }

  play(pcm: Int16Array) {
    const now = this.ctx.currentTime
    if (this.next < now) this.next = now + 0.15
    else if (this.next > now + 1) return // fallen behind: skip a piece to catch up
    const buffer = this.ctx.createBuffer(1, pcm.length, PCM_RATE)
    const data = buffer.getChannelData(0)
    for (let i = 0; i < pcm.length; i++) data[i] = pcm[i] / 32768
    const node = this.ctx.createBufferSource()
    node.buffer = buffer
    node.connect(this.ctx.destination)
    node.start(this.next)
    this.next += buffer.duration
  }

  close() {
    this.ctx.close().catch(() => {})
  }
}
