export interface Extension {
  id: string;
  name: string;
  description: string;
  code: string;
  type: 'transform' | 'pre_processor';
  createdAt: string;
  updatedAt: string;
}

export interface IndexExtensionModel extends Extension {
  runOrder: number;
  enabled: boolean;
}
