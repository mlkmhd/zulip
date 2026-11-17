const MICROS_PER_SECOND: i64 = 1_000_000;
const END_OF_YEAR_9999: f64 = 253_402_300_800.0;

#[derive(Debug, PartialEq, Eq)]
pub enum TimestampError {
    Negative,
    OutOfRange,
}

pub fn unix_microseconds(timestamp: f64) -> Result<i64, TimestampError> {
    if timestamp < -1.0 {
        return Err(TimestampError::Negative);
    }
    if !timestamp.is_finite() || timestamp >= END_OF_YEAR_9999 {
        return Err(TimestampError::OutOfRange);
    }

    let scale = MICROS_PER_SECOND as f64;
    // From 2^33 seconds onward, adjacent floats are more than a microsecond
    // apart. CPython's round(timestamp, 6) then returns the original float.
    let micros = if timestamp < 8_589_934_592.0 {
        let scaled = timestamp * scale;
        let floor = scaled.floor();
        if scaled - floor == 0.5 {
            // Multiplication can turn a value near a midpoint into an exact
            // midpoint. Recover the error to round the original binary float.
            let error = timestamp.mul_add(scale, -scaled);
            if error < 0.0 {
                floor as i64
            } else if error > 0.0 {
                floor as i64 + 1
            } else {
                scaled.round_ties_even() as i64
            }
        } else {
            scaled.round_ties_even() as i64
        }
    } else {
        // Match datetime.fromtimestamp by rounding the fractional part
        // separately, so large timestamps retain their microseconds.
        timestamp.trunc() as i64 * MICROS_PER_SECOND
            + (timestamp.fract() * scale).round_ties_even() as i64
    };

    if micros < 0 {
        return Err(TimestampError::Negative);
    }
    Ok(micros)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounds_to_microseconds_like_cpython() {
        for (timestamp, micros) in [
            (-0.000_000_5, 0),
            (0.000_001_5, 2),
            (0.007_812_5, 7_812),
            (0.023_437_5, 23_438),
            (1.999_999_5, 1_999_999),
            (1.999_999_6, 2_000_000),
            (8_589_934_591.999_999, 8_589_934_591_999_999),
            (8_589_934_592.000_002, 8_589_934_592_000_002),
            (253_402_300_799.999_97, 253_402_300_799_999_969),
        ] {
            assert_eq!(unix_microseconds(timestamp), Ok(micros), "{timestamp}");
        }
    }

    #[test]
    fn rejects_negative_and_out_of_range_timestamps() {
        for timestamp in [-0.000_000_500_000_000_001, -1.0, f64::NEG_INFINITY] {
            assert_eq!(unix_microseconds(timestamp), Err(TimestampError::Negative));
        }
        for timestamp in [END_OF_YEAR_9999, f64::INFINITY, f64::NAN] {
            assert_eq!(
                unix_microseconds(timestamp),
                Err(TimestampError::OutOfRange)
            );
        }
    }
}
