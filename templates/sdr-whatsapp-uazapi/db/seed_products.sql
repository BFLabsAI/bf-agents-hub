-- Seed de produtos genéricos do template.
-- Mesma convenção de substituição {PREFIX} do schema.sql.
-- Espelha IDENTITY["products"] em config/identity.py.

INSERT INTO {PREFIX}products (slug, name, description, vendor_key, active)
VALUES
    (
        'plano-essencial',
        'Plano Essencial',
        'Solução de entrada focada no diagnóstico inicial e resolução rápida dos principais gargalos.',
        'consultor_padrao',
        TRUE
    ),
    (
        'plano-avancado',
        'Plano Avançado',
        'Programa completo de acompanhamento e aceleração com suporte prioritário.',
        'consultor_padrao',
        TRUE
    )
ON CONFLICT (slug) DO UPDATE
    SET name        = EXCLUDED.name,
        description = EXCLUDED.description,
        vendor_key  = EXCLUDED.vendor_key,
        active      = EXCLUDED.active;
