> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/IMPL009-amplitude_analyzer_implementation.md` on 2026-08-09. **Not a Lempi specification.**
>
> Implementation notes for the amplitude analyzer: parameter presets per source medium.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# WKMP Amplitude Analyzer Implementation

**⚙️ TIER 3 - IMPLEMENTATION SPECIFICATION**

Defines Rust implementation for amplitude-based lead-in/lead-out detection. Derived from [SPEC025](MCR-SPEC025-amplitude_analysis.md). See Document Hierarchy.

> **Related:** [Amplitude Analysis](MCR-SPEC025-amplitude_analysis.md) | Audio Ingest Architecture

---

## Overview

**Module:** `wkmp-ai/src/services/amplitude_analyzer.rs`
**Purpose:** Implement RMS-based amplitude analysis for automatic lead-in/lead-out detection
**Dependencies:** `dasp` (RMS), `symphonia` (audio decode), `rubato` (resampling)

---

## Module Structure

```rust
// wkmp-ai/src/services/amplitude_analyzer.rs

use dasp::signal::Signal;
use std::path::Path;

/// Parameters for amplitude analysis
#[derive(Debug, Clone)]
pub struct AmplitudeParameters {
    pub rms_window_ms: u32,              // Default: 100
    pub lead_in_threshold_db: f32,       // Default: -12.0
    pub lead_out_threshold_db: f32,      // Default: -12.0
    pub quick_ramp_threshold: f32,       // Default: 0.75
    pub quick_ramp_duration_s: f32,      // Default: 1.0
    pub max_lead_in_duration_s: f32,     // Default: 5.0
    pub max_lead_out_duration_s: f32,    // Default: 5.0
    pub apply_a_weighting: bool,         // Default: true
}

impl Default for AmplitudeParameters {
    fn default() -> Self {
        Self {
            rms_window_ms: 100,
            lead_in_threshold_db: -12.0,
            lead_out_threshold_db: -12.0,
            quick_ramp_threshold: 0.75,
            quick_ramp_duration_s: 1.0,
            max_lead_in_duration_s: 5.0,
            max_lead_out_duration_s: 5.0,
            apply_a_weighting: true,
        }
    }
}

/// Result of amplitude analysis
#[derive(Debug, Clone)]
pub struct AmplitudeAnalysisResult {
    pub peak_rms: f32,
    pub lead_in_duration: f32,          // Seconds
    pub lead_out_duration: f32,         // Seconds
    pub quick_ramp_up: bool,
    pub quick_ramp_down: bool,
    pub rms_profile: Vec<f32>,          // RMS envelope
}

/// Amplitude analyzer service
pub struct AmplitudeAnalyzer {
    params: AmplitudeParameters,
}

impl AmplitudeAnalyzer {
    pub fn new(params: AmplitudeParameters) -> Self {
        Self { params }
    }

    /// Analyze audio file for lead-in/lead-out timing
    pub async fn analyze_file(
        &self,
        file_path: &Path,
        start_time: f32,
        end_time: f32,
    ) -> Result<AmplitudeAnalysisResult, AnalysisError> {
        // Implementation below
    }

    /// Calculate RMS envelope from audio samples
    fn calculate_rms_envelope(
        &self,
        samples: &[f32],
        sample_rate: u32,
    ) -> Vec<f32> {
        // Implementation below
    }

    /// Apply A-weighting filter to audio samples
    fn apply_a_weighting(&self, samples: &[f32], sample_rate: u32) -> Vec<f32> {
        // Implementation below
    }

    /// Detect lead-in duration
    fn detect_lead_in(&self, rms_envelope: &[f32], peak_rms: f32) -> f32 {
        // Implementation below
    }

    /// Detect lead-out duration
    fn detect_lead_out(&self, rms_envelope: &[f32], peak_rms: f32) -> f32 {
        // Implementation below
    }
}

#[derive(Debug, thiserror::Error)]
pub enum AnalysisError {
    #[error("Failed to decode audio: {0}")]
    DecodeError(String),
    
    #[error("Audio file too quiet (peak RMS < 0.05)")]
    TooQuiet,
    
    #[error("Audio clipping detected")]
    ClippingDetected,
    
    #[error("Invalid time range: start={0}, end={1}")]
    InvalidTimeRange(f32, f32),
}
```

---

## Implementation Details

### Audio Decoding

```rust
pub async fn analyze_file(
    &self,
    file_path: &Path,
    start_time: f32,
    end_time: f32,
) -> Result<AmplitudeAnalysisResult, AnalysisError> {
    // 1. Decode audio using symphonia
    let file = std::fs::File::open(file_path)
        .map_err(|e| AnalysisError::DecodeError(e.to_string()))?;
    
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    let mut format_reader = symphonia::default::get_probe()
        .format(&Default::default(), mss, &Default::default(), &Default::default())
        .map_err(|e| AnalysisError::DecodeError(e.to_string()))?
        .format;
    
    let track = format_reader.default_track()
        .ok_or_else(|| AnalysisError::DecodeError("No audio track".to_string()))?;
    
    let sample_rate = track.codec_params.sample_rate
        .ok_or_else(|| AnalysisError::DecodeError("No sample rate".to_string()))?;
    
    // 2. Decode to PCM samples
    let mut decoder = symphonia::default::get_codecs()
        .make(&track.codec_params, &Default::default())
        .map_err(|e| AnalysisError::DecodeError(e.to_string()))?;
    
    let mut samples = Vec::new();
    let start_sample = (start_time * sample_rate as f32) as u64;
    let end_sample = (end_time * sample_rate as f32) as u64;
    
    // Decode packets, collect samples in range
    while let Ok(packet) = format_reader.next_packet() {
        let decoded = decoder.decode(&packet)
            .map_err(|e| AnalysisError::DecodeError(e.to_string()))?;
        
        // Convert to f32 mono samples (mix down if stereo)
        let pcm_samples = convert_to_mono_f32(decoded);
        samples.extend_from_slice(&pcm_samples);
    }
    
    // Extract time range
    let samples = &samples[start_sample as usize..end_sample as usize];
    
    // 3. Apply A-weighting if enabled
    let weighted_samples = if self.params.apply_a_weighting {
        self.apply_a_weighting(samples, sample_rate)
    } else {
        samples.to_vec()
    };
    
    // 4. Calculate RMS envelope
    let rms_envelope = self.calculate_rms_envelope(&weighted_samples, sample_rate);
    
    // 5. Find peak RMS
    let peak_rms = rms_envelope.iter()
        .copied()
        .max_by(|a, b| a.partial_cmp(b).unwrap())
        .unwrap_or(0.0);
    
    // Check for very quiet audio
    if peak_rms < 0.05 {
        return Err(AnalysisError::TooQuiet);
    }
    
    // 6. Detect lead-in and lead-out
    let lead_in_duration = self.detect_lead_in(&rms_envelope, peak_rms);
    let lead_out_duration = self.detect_lead_out(&rms_envelope, peak_rms);
    
    Ok(AmplitudeAnalysisResult {
        peak_rms,
        lead_in_duration,
        lead_out_duration,
        quick_ramp_up: lead_in_duration == 0.0,
        quick_ramp_down: lead_out_duration == 0.0,
        rms_profile: rms_envelope,
    })
}
```

### RMS Envelope Calculation

```rust
fn calculate_rms_envelope(
    &self,
    samples: &[f32],
    sample_rate: u32,
) -> Vec<f32> {
    let window_size = ((self.params.rms_window_ms as f32 / 1000.0) 
        * sample_rate as f32) as usize;
    
    let mut rms_envelope = Vec::new();
    
    for i in (0..samples.len()).step_by(window_size) {
        let window_end = (i + window_size).min(samples.len());
        let window = &samples[i..window_end];
        
        // Calculate RMS: sqrt(mean(samples^2))
        let sum_squares: f32 = window.iter()
            .map(|&s| s * s)
            .sum();
        let mean_square = sum_squares / window.len() as f32;
        let rms = mean_square.sqrt();
        
        rms_envelope.push(rms);
    }
    
    rms_envelope
}
```

### A-Weighting Filter

```rust
fn apply_a_weighting(&self, samples: &[f32], sample_rate: u32) -> Vec<f32> {
    // Standard A-weighting filter (IIR biquad)
    // Coefficients for 44100 Hz (adjust for other sample rates)
    
    let (b0, b1, b2, a1, a2) = if sample_rate == 44100 {
        // Pre-calculated coefficients for 44.1 kHz
        (0.169994948147430, 0.0, -0.169994948147430,
         -1.584097348918614, 0.821978466603620)
    } else {
        // Simplified: return unfiltered for non-standard rates
        return samples.to_vec();
    };
    
    let mut filtered = Vec::with_capacity(samples.len());
    let mut x1 = 0.0;
    let mut x2 = 0.0;
    let mut y1 = 0.0;
    let mut y2 = 0.0;
    
    for &sample in samples {
        let y = b0 * sample + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
        
        x2 = x1;
        x1 = sample;
        y2 = y1;
        y1 = y;
        
        filtered.push(y);
    }
    
    filtered
}
```

### Lead-In Detection

```rust
fn detect_lead_in(&self, rms_envelope: &[f32], peak_rms: f32) -> f32 {
    let window_duration = self.params.rms_window_ms as f32 / 1000.0;
    
    // Calculate thresholds
    let threshold_25 = peak_rms * 10f32.powf(self.params.lead_in_threshold_db / 20.0);
    let threshold_75 = peak_rms * 10f32.powf(-5.0 / 20.0);
    
    // Detect quick ramp-up
    for (i, &rms) in rms_envelope.iter().enumerate() {
        if rms >= threshold_75 {
            let time_to_75 = (i as f32 * window_duration);
            if time_to_75 < self.params.quick_ramp_duration_s {
                return 0.0; // Quick ramp-up
            }
            break;
        }
    }
    
    // Find slow ramp-up lead-in point
    for (i, &rms) in rms_envelope.iter().enumerate() {
        if rms >= threshold_25 {
            let lead_in = i as f32 * window_duration;
            return lead_in.min(self.params.max_lead_in_duration_s);
        }
    }
    
    0.0 // No lead-in detected
}
```

### Lead-Out Detection

```rust
fn detect_lead_out(&self, rms_envelope: &[f32], peak_rms: f32) -> f32 {
    let window_duration = self.params.rms_window_ms as f32 / 1000.0;
    
    let threshold_25 = peak_rms * 10f32.powf(self.params.lead_out_threshold_db / 20.0);
    let threshold_75 = peak_rms * 10f32.powf(-5.0 / 20.0);
    
    // Detect quick ramp-down (search from end backward)
    for i in (0..rms_envelope.len()).rev() {
        if rms_envelope[i] >= threshold_75 {
            let time_from_75 = ((rms_envelope.len() - i) as f32 * window_duration);
            if time_from_75 < self.params.quick_ramp_duration_s {
                return 0.0; // Quick ramp-down
            }
            break;
        }
    }
    
    // Find slow ramp-down lead-out point
    for i in (0..rms_envelope.len()).rev() {
        if rms_envelope[i] >= threshold_25 {
            let lead_out = ((rms_envelope.len() - i) as f32 * window_duration);
            return lead_out.min(self.params.max_lead_out_duration_s);
        }
    }
    
    0.0 // No lead-out detected
}
```

---

## Testing Strategy

### Unit Tests

```rust
#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_slow_fade_in() {
        // Generate synthetic audio with gradual crescendo
        let mut samples = Vec::new();
        for i in 0..132300 { // 3 seconds at 44.1kHz
            let t = i as f32 / 44100.0;
            let amplitude = (t / 3.0).min(1.0); // Linear ramp over 3s
            samples.push(amplitude * (t * 440.0 * 2.0 * PI).sin());
        }
        
        let analyzer = AmplitudeAnalyzer::new(AmplitudeParameters::default());
        let rms = analyzer.calculate_rms_envelope(&samples, 44100);
        let peak = rms.iter().copied().max_by(|a, b| a.partial_cmp(b).unwrap()).unwrap();
        let lead_in = analyzer.detect_lead_in(&rms, peak);
        
        assert!(lead_in > 2.0 && lead_in < 3.5, "Expected lead-in ~2.5-3s, got {}", lead_in);
    }
    
    #[test]
    fn test_quick_attack() {
        // Generate audio with sudden drum hit
        let mut samples = vec![0.0; 4410]; // 100ms silence
        samples.extend(vec![0.8; 132300]); // Immediate loud sound
        
        let analyzer = AmplitudeAnalyzer::new(AmplitudeParameters::default());
        let rms = analyzer.calculate_rms_envelope(&samples, 44100);
        let peak = rms.iter().copied().max_by(|a, b| a.partial_cmp(b).unwrap()).unwrap();
        let lead_in = analyzer.detect_lead_in(&rms, peak);
        
        assert_eq!(lead_in, 0.0, "Quick attack should have 0 lead-in");
    }
}
```

---

## Dependencies (Cargo.toml)

```toml
[dependencies]
symphonia = { version = "0.5", features = ["mp3", "flac", "ogg", "aac"] }
dasp = "0.11"
rubato = "0.14"
thiserror = "1.0"
```

---

**Document Version:** 1.0
**Last Updated:** 2025-10-27
