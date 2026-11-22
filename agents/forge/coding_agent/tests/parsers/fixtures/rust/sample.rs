pub struct User { pub id: u64 }

pub trait Repository { fn find(&self); }
