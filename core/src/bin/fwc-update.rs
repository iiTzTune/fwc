#[path = "../update/mod.rs"]
mod update;

fn main() -> anyhow::Result<()> {
    update::run(std::env::args().skip(1).collect())
}
