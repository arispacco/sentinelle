import 'dart:convert';
import 'package:path/path.dart';
import 'package:sqflite/sqflite.dart';

/// Cache offline SQLite des fiches déjà consultées.
class OfflineCache {
  Database? _db;

  Future<Database> get db async {
    if (_db != null) return _db!;
    final path = join(await getDatabasesPath(), 'medical_cache.db');
    _db = await openDatabase(
      path,
      version: 1,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE cached_records (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            cached_at TEXT NOT NULL
          )
        ''');
      },
    );
    return _db!;
  }

  Future<void> putRecord(Map<String, dynamic> record) async {
    final id = record['id']?.toString();
    if (id == null) return;
    final database = await db;
    await database.insert(
      'cached_records',
      {
        'id': id,
        'payload': jsonEncode(record),
        'cached_at': DateTime.now().toUtc().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<Map<String, dynamic>?> getRecord(String id) async {
    final database = await db;
    final rows = await database.query('cached_records', where: 'id = ?', whereArgs: [id], limit: 1);
    if (rows.isEmpty) return null;
    return jsonDecode(rows.first['payload'] as String) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listRecords({int limit = 50}) async {
    final database = await db;
    final rows = await database.query('cached_records', orderBy: 'cached_at DESC', limit: limit);
    return rows
        .map((r) => jsonDecode(r['payload'] as String) as Map<String, dynamic>)
        .toList();
  }
}
