//! Built-in output comparison strategies (`exact`, `tokens`, `float:<eps>`).

use crate::model::options::CompareMode;

/// Compare `expected` and `actual` outputs using `mode`.
///
/// Returns `Ok(())` when outputs match, or `Err(reason)` describing the first mismatch.
pub fn compare_outputs(expected: &str, actual: &str, mode: CompareMode) -> Result<(), String> {
    match mode {
        CompareMode::Exact => {
            if expected.trim() == actual.trim() {
                Ok(())
            } else {
                Err("Output mismatch (exact)".to_string())
            }
        }
        CompareMode::Tokens => {
            let exp_tokens: Vec<&str> = expected.split_whitespace().collect();
            let act_tokens: Vec<&str> = actual.split_whitespace().collect();
            if exp_tokens.len() != act_tokens.len() {
                return Err(format!(
                    "Token count mismatch: expected {} token(s), got {}",
                    exp_tokens.len(),
                    act_tokens.len()
                ));
            }
            for (idx, (e, a)) in exp_tokens.iter().zip(act_tokens.iter()).enumerate() {
                if e != a {
                    return Err(format!(
                        "Token #{} mismatch: expected '{}', got '{}'",
                        idx + 1,
                        e,
                        a
                    ));
                }
            }
            Ok(())
        }
        CompareMode::Float(eps) => {
            let exp_tokens: Vec<&str> = expected.split_whitespace().collect();
            let act_tokens: Vec<&str> = actual.split_whitespace().collect();
            if exp_tokens.len() != act_tokens.len() {
                return Err(format!(
                    "Token count mismatch: expected {} token(s), got {}",
                    exp_tokens.len(),
                    act_tokens.len()
                ));
            }
            for (idx, (e, a)) in exp_tokens.iter().zip(act_tokens.iter()).enumerate() {
                if e == a {
                    continue;
                }
                match (e.parse::<f64>(), a.parse::<f64>()) {
                    (Ok(ev), Ok(av)) if ev.is_finite() && av.is_finite() => {
                        let scale = ev.abs().max(av.abs()).max(1.0);
                        if (ev - av).abs() > eps * scale {
                            return Err(format!(
                                "Float token #{} mismatch: expected {ev}, got {av} (eps={eps:e})",
                                idx + 1
                            ));
                        }
                    }
                    _ => {
                        return Err(format!(
                            "Token #{} mismatch: expected '{}', got '{}'",
                            idx + 1,
                            e,
                            a
                        ));
                    }
                }
            }
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    #[test]
    fn test_compare_mode_parsing() {
        assert_eq!(CompareMode::from_str("exact").unwrap(), CompareMode::Exact);
        assert_eq!(CompareMode::from_str("tokens").unwrap(), CompareMode::Tokens);
        assert_eq!(CompareMode::from_str("float").unwrap(), CompareMode::Float(1e-6));
        assert_eq!(
            CompareMode::from_str("float:1e-4").unwrap(),
            CompareMode::Float(1e-4)
        );
        assert!(CompareMode::from_str("float:-1").is_err());
        assert!(CompareMode::from_str("unknown").is_err());
    }

    #[test]
    fn test_compare_tokens_and_float() {
        assert!(compare_outputs("1  2\n3\n", "1 2 3", CompareMode::Tokens).is_ok());
        assert!(compare_outputs("1 2", "1 2 3", CompareMode::Tokens).is_err());

        assert!(
            compare_outputs("ans: 3.1415926", "ans: 3.1415930", CompareMode::Float(1e-5)).is_ok()
        );
        assert!(
            compare_outputs("ans: 3.14", "ans: 3.20", CompareMode::Float(1e-5)).is_err()
        );
        assert!(
            compare_outputs("1000000.0", "1000000.5", CompareMode::Float(1e-6)).is_ok()
        );
    }
}
