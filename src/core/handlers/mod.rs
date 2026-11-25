//! Built-in and custom language execution handlers.
//!
//! Each handler implements [`crate::core::registry::LanguageHandler`] for a
//! specific language family or custom `[[languages]]` rule.

pub mod c_family;
pub mod custom_handler;
pub mod java_handler;
pub mod python_handler;
pub mod rust_handler;
pub mod script_handler;

pub use c_family::{CFamilyHandler, CPM};
pub use custom_handler::CustomLanguageHandler;
pub use java_handler::JavaHandler;
pub use python_handler::PythonHandler;
pub use rust_handler::RustHandler;
pub use script_handler::ScriptHandler;
